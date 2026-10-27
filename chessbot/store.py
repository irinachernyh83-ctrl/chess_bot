"""Хранение партий в SQLite — состояние переживает перезапуск бота.

Партия живёт в одном или двух чатах:
* группа — оба игрока пишут в один чат;
* личка — у каждого игрока свой чат с ботом, партия связывает их по id.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import chess

STATUS_WAITING = "waiting"
STATUS_ACTIVE = "active"
STATUS_FINISHED = "finished"

MODE_GROUP = "group"
MODE_INVITE = "invite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mode           TEXT    NOT NULL DEFAULT 'group',
    white_id       INTEGER,
    white_name     TEXT    NOT NULL DEFAULT '',
    black_id       INTEGER,
    black_name     TEXT    NOT NULL DEFAULT '',
    white_chat     INTEGER NOT NULL,
    black_chat     INTEGER,
    status         TEXT    NOT NULL,
    fen            TEXT    NOT NULL,
    moves          TEXT    NOT NULL DEFAULT '',
    result_text    TEXT,
    last_move      TEXT,
    last_move_uci  TEXT,
    msg_white      INTEGER,
    msg_black      INTEGER,
    invite_token   TEXT,
    base_ms        INTEGER NOT NULL DEFAULT 0,
    white_ms       INTEGER NOT NULL DEFAULT 0,
    black_ms       INTEGER NOT NULL DEFAULT 0,
    turn_started_at REAL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS games_invite_token ON games(invite_token);
CREATE INDEX IF NOT EXISTS games_chats ON games(white_chat, black_chat);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Game:
    id: int | None = None
    mode: str = MODE_GROUP
    white_id: int | None = None
    white_name: str = ""
    black_id: int | None = None
    black_name: str = ""
    white_chat: int = 0
    black_chat: int | None = None
    status: str = STATUS_WAITING
    fen: str = chess.STARTING_FEN
    moves: str = ""
    result_text: str | None = None
    last_move: str | None = None
    last_move_uci: str | None = None
    msg_white: int | None = None
    msg_black: int | None = None
    invite_token: str | None = None
    base_ms: int = 0
    white_ms: int = 0
    black_ms: int = 0
    turn_started_at: float | None = None
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------- состояние
    def board(self) -> chess.Board:
        """Позиция с полной историей ходов (нужна для правила повторений)."""
        if not self.moves:
            return chess.Board(self.fen)
        board = chess.Board()
        try:
            for uci in self.moves.split():
                board.push_uci(uci)
        except ValueError:
            return chess.Board(self.fen)
        return board

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    @property
    def is_waiting(self) -> bool:
        return self.status == STATUS_WAITING

    @property
    def is_finished(self) -> bool:
        return self.status == STATUS_FINISHED

    @property
    def is_invite(self) -> bool:
        return self.mode == MODE_INVITE

    # ------------------------------------------------------------------ чаты
    def targets(self) -> list[tuple[int, str]]:
        """Куда отправлять доску: (chat_id, слот сообщения)."""
        if not self.black_chat or self.black_chat == self.white_chat:
            return [(self.white_chat, "white")]
        return [(self.white_chat, "white"), (self.black_chat, "black")]

    def chat_of(self, color: chess.Color) -> int:
        if color == chess.WHITE:
            return self.white_chat
        return self.black_chat or self.white_chat

    def opponent_chat(self, color: chess.Color) -> int:
        return self.chat_of(not color)

    def user_of(self, color: chess.Color) -> int | None:
        return self.white_id if color == chess.WHITE else self.black_id

    def name_of(self, color: chess.Color) -> str:
        return self.white_name if color == chess.WHITE else self.black_name

    def color_of_user(self, user_id: int) -> chess.Color | None:
        if user_id == self.white_id:
            return chess.WHITE
        if user_id == self.black_id:
            return chess.BLACK
        return None

    def has_both_players(self) -> bool:
        return self.white_id is not None and self.black_id is not None

    # ----------------------------------------------------------------- часы
    @property
    def timed(self) -> bool:
        return self.base_ms > 0

    def stored_ms(self, color: chess.Color) -> int:
        return self.white_ms if color == chess.WHITE else self.black_ms

    def set_stored_ms(self, color: chess.Color, value: int) -> None:
        value = max(0, int(value))
        if color == chess.WHITE:
            self.white_ms = value
        else:
            self.black_ms = value

    def remaining_ms(self, color: chess.Color, now: float | None = None) -> int | None:
        """Сколько осталось у игрока с учётом идущих часов."""
        if not self.timed:
            return None
        stored = self.stored_ms(color)
        if self.status != STATUS_ACTIVE or self.turn_started_at is None:
            return stored
        if self.board().turn != color:
            return stored
        elapsed = int((now if now is not None else time.time()) - self.turn_started_at)
        return max(0, stored - elapsed * 1000)

    def flagged_color(self, now: float | None = None) -> chess.Color | None:
        """Кто просрочил время (если партия идёт по часам)."""
        if not self.is_active or not self.timed:
            return None
        turn = self.board().turn
        remaining = self.remaining_ms(turn, now)
        return turn if remaining is not None and remaining <= 0 else None

    def start_clock(self, now: float | None = None) -> None:
        if not self.timed:
            return
        self.white_ms = self.base_ms
        self.black_ms = self.base_ms
        self.turn_started_at = now if now is not None else time.time()

    def spend_time(self, color: chess.Color, now: float | None = None) -> int:
        """Списывает время хода с игрока и запускает часы соперника."""
        if not self.timed:
            return 0
        now = now if now is not None else time.time()
        spent = 0
        if self.turn_started_at is not None:
            spent = max(0, int((now - self.turn_started_at) * 1000))
            self.set_stored_ms(color, self.stored_ms(color) - spent)
        self.turn_started_at = now
        return spent


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()
            self._conn.executescript(_SCHEMA)

    def _migrate(self) -> None:
        """Старая схема (партия на чат) уводится в архив, данные не теряются."""
        rows = self._conn.execute("PRAGMA table_info(games)").fetchall()
        if not rows:
            return
        columns = {row["name"] for row in rows}
        if "id" in columns:
            return
        legacy = f"games_legacy_{int(time.time())}"
        self._conn.execute(f'ALTER TABLE games RENAME TO "{legacy}"')

    # ------------------------------------------------------------------ CRUD
    def get(self, game_id: int | None) -> Game | None:
        if game_id is None:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return self._row_to_game(row) if row else None

    def current(self, chat_id: int) -> Game | None:
        """Текущая партия чата: сначала незавершённые, потом последняя оконченная."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM games
                WHERE white_chat = ? OR black_chat = ?
                ORDER BY CASE status WHEN 'finished' THEN 1 ELSE 0 END, id DESC
                LIMIT 1
                """,
                (chat_id, chat_id),
            ).fetchone()
        return self._row_to_game(row) if row else None

    def get_by_token(self, token: str) -> Game | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM games WHERE invite_token = ?", (token,)
            ).fetchone()
        return self._row_to_game(row) if row else None

    def active_with_clock(self) -> list[Game]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM games WHERE status = ? AND base_ms > 0",
                (STATUS_ACTIVE,),
            ).fetchall()
        return [self._row_to_game(row) for row in rows]

    def finished_older_than(
        self, seconds: float, now: float | None = None
    ) -> list[Game]:
        """Оконченные партии, которые завершились раньше указанного срока."""
        cutoff = datetime.fromtimestamp(
            (now if now is not None else time.time()) - max(0.0, seconds),
            timezone.utc,
        ).isoformat(timespec="seconds")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM games WHERE status = ? AND updated_at < ?",
                (STATUS_FINISHED, cutoff),
            ).fetchall()
        return [self._row_to_game(row) for row in rows]

    def save(self, game: Game) -> Game:
        game.updated_at = _now_iso()
        if not game.created_at:
            game.created_at = game.updated_at
        with self._lock, self._conn:
            if game.id is None:
                cursor = self._conn.execute(
                    """
                    INSERT INTO games (mode, white_id, white_name, black_id, black_name,
                                       white_chat, black_chat, status, fen, moves, result_text,
                                       last_move, last_move_uci, msg_white, msg_black, invite_token,
                                       base_ms, white_ms, black_ms, turn_started_at,
                                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game.mode, game.white_id, game.white_name, game.black_id, game.black_name,
                        game.white_chat, game.black_chat, game.status, game.fen, game.moves,
                        game.result_text, game.last_move, game.last_move_uci, game.msg_white,
                        game.msg_black, game.invite_token, game.base_ms, game.white_ms,
                        game.black_ms, game.turn_started_at, game.created_at, game.updated_at,
                    ),
                )
                game.id = int(cursor.lastrowid)
            else:
                self._conn.execute(
                    """
                    UPDATE games SET
                        mode=?, white_id=?, white_name=?, black_id=?, black_name=?,
                        white_chat=?, black_chat=?, status=?, fen=?, moves=?, result_text=?,
                        last_move=?, last_move_uci=?, msg_white=?, msg_black=?, invite_token=?,
                        base_ms=?, white_ms=?, black_ms=?, turn_started_at=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        game.mode, game.white_id, game.white_name, game.black_id, game.black_name,
                        game.white_chat, game.black_chat, game.status, game.fen, game.moves,
                        game.result_text, game.last_move, game.last_move_uci, game.msg_white,
                        game.msg_black, game.invite_token, game.base_ms, game.white_ms,
                        game.black_ms, game.turn_started_at, game.updated_at, game.id,
                    ),
                )
        return game

    def delete(self, game_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM games WHERE id = ?", (game_id,))

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _row_to_game(row: sqlite3.Row) -> Game:
        return Game(
            id=row["id"],
            mode=row["mode"],
            white_id=row["white_id"],
            white_name=row["white_name"],
            black_id=row["black_id"],
            black_name=row["black_name"],
            white_chat=row["white_chat"],
            black_chat=row["black_chat"],
            status=row["status"],
            fen=row["fen"],
            moves=row["moves"],
            result_text=row["result_text"],
            last_move=row["last_move"],
            last_move_uci=row["last_move_uci"],
            msg_white=row["msg_white"],
            msg_black=row["msg_black"],
            invite_token=row["invite_token"],
            base_ms=row["base_ms"],
            white_ms=row["white_ms"],
            black_ms=row["black_ms"],
            turn_started_at=row["turn_started_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
