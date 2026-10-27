"""Обработчики Telegram на Telethon (MTProto): ходы, команды, кнопки.

Поддерживаются два режима:
* группа — оба игрока пишут в один чат;
* личка — белые создают партию и отправляют другу ссылку-приглашение,
  соперник принимает её в своём личном чате с ботом.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import secrets
import time

import chess
from telethon import Button, events
from telethon.tl import functions, types

from . import board as board_render
from . import invites, keyboards, notation, texts
from .store import (
    MODE_GROUP,
    MODE_INVITE,
    STATUS_ACTIVE,
    STATUS_FINISHED,
    STATUS_WAITING,
    Game,
    Store,
)

log = logging.getLogger(__name__)

KEYWORDS: dict[str, tuple[str, ...]] = {
    "new": ("новая игра", "новая партия", "новую игру", "new game", "new", "старт"),
    "join": ("присоединиться", "присоединяюсь", "я в игре", "в игру", "join"),
    "resign": ("сдаюсь", "сдаться", "resign"),
    "draw": ("предлагаю ничью", "ничья", "ничью", "draw"),
    "board": ("покажи доску", "доска", "board"),
    "help": ("как играть", "правила", "помощь", "help"),
    "cancel": ("отменить партию", "отмена партии", "отменить", "отмена", "cancel"),
    "menu": ("меню", "menu", "кнопки"),
}

COMMANDS = [
    ("new", "🆕 Новая партия"),
    ("board", "♟ Показать доску"),
    ("draw", "🤝 Предложить ничью"),
    ("resign", "🏳️ Сдаться"),
    ("cancel", "❌ Отменить партию"),
    ("menu", "📱 Показать меню"),
    ("help", "❓ Как играть"),
]

CLOCK_TICK = 3.0
CLEANUP_TICK = 30.0
# пороги предупреждений в секундах (от меньшего к большему)
WARNING_STEPS = (60, 120, 180)


def display_name(user) -> str:
    if user is None:
        return "Игрок"
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(p for p in parts if p).strip()
    return name or getattr(user, "username", None) or "Игрок"


def _photo(png: bytes) -> io.BytesIO:
    buf = io.BytesIO(png)
    buf.name = "board.png"  # Telethon по расширению понимает, что это фото
    return buf


class InlineGame:
    """Партия, которая целиком живёт в инлайн-сообщении.

    Бот не состоит в чате: он редактирует своё инлайн-сообщение, а игроки
    ходят нажатиями по клеткам. Поэтому такая партия не занимает чат
    и не требует, чтобы соперник запускал бота.
    """

    def __init__(self, token: str, msg_id, game: Game):
        self.token = token
        self.msg_id = msg_id
        self.game = game
        self.selected: dict[int, str] = {}
        self.draw_offer_by: int | None = None
        self.resign_pending_by: int | None = None
        self.created_at = time.time()
        self.finished_at: float | None = None
        # цвет -> уже отправленные пороги (в секундах)
        self.warned: dict[chess.Color, set[int]] = {}
        # последнее предупреждение о времени, видимое в подписи доски
        self.warning_text: str | None = None


class ChessBot:
    def __init__(
        self,
        client,
        store: Store,
        inline_secret: bytes = b"",
        finished_ttl: float = 600.0,
    ):
        self.client = client
        self.store = store
        self.inline_secret = inline_secret
        self.finished_ttl = max(0.0, float(finished_ttl))
        self.me = None
        self._locks: dict[str, asyncio.Lock] = {}
        # чат -> (кто создаёт партию, имя): ждём выбор контроля времени
        self._pending: dict[int, tuple[int, str]] = {}
        self._clock_task: asyncio.Task | None = None
        # инлайн-партии: token -> InlineGame и inline message bytes -> InlineGame
        self._inline_games: dict[str, InlineGame] = {}
        self._inline_by_message: dict[bytes, InlineGame] = {}
        # (id партии, цвет) -> уже отправленные предупреждения о времени
        self._warned: dict[tuple[int, chess.Color], set[int]] = {}
        self._last_cleanup = 0.0

    # ------------------------------------------------------------------ infra
    async def setup(self) -> None:
        self.me = await self.client.get_me()
        try:
            await self.client(
                functions.bots.SetBotCommandsRequest(
                    scope=types.BotCommandScopeDefault(),
                    lang_code="",
                    commands=[types.BotCommand(command=c, description=d) for c, d in COMMANDS],
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось обновить меню команд: %s", exc)
        log.info("Бот запущен: @%s (id=%s)", self.username, self.me.id)

    def start_clock_watch(self) -> None:
        if self._clock_task is None or self._clock_task.done():
            self._clock_task = asyncio.create_task(self._watch_clocks())

    async def stop(self) -> None:
        if self._clock_task and not self._clock_task.done():
            self._clock_task.cancel()
            try:
                await self._clock_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    @property
    def username(self) -> str:
        return (getattr(self.me, "username", None) or "").lower()

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _chat_key(chat_id: int) -> str:
        return f"chat:{chat_id}"

    @staticmethod
    def _game_key(game_id: int | None) -> str:
        return f"game:{game_id}"

    # --------------------------------------------------------------- отправка
    @staticmethod
    def _reply_to(event) -> int | None:
        """Id сообщения, на которое отвечаем.

        У ``CallbackQuery`` (Telethon 1.42) атрибута ``message`` нет вовсе:
        есть только ``message_id`` и ``get_message()``. Поэтому через ``getattr``.
        """
        message = getattr(event, "message", None)
        return getattr(message, "id", None)

    async def _reply(self, event, text: str) -> None:
        await self._notify(event.chat_id, text, reply_to=self._reply_to(event))

    async def _notify(
        self,
        chat_id: int,
        text: str,
        *,
        buttons=None,
        reply_to: int | None = None,
    ) -> None:
        try:
            await self.client.send_message(
                chat_id,
                text,
                parse_mode="html",
                link_preview=False,
                buttons=buttons,
                reply_to=reply_to,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось отправить сообщение в %s: %s", chat_id, exc)
            if buttons is None:
                return
            # клавиатура не должна «съедать» сообщение: пробуем без неё
            try:
                await self.client.send_message(
                    chat_id,
                    text,
                    parse_mode="html",
                    link_preview=False,
                    reply_to=reply_to,
                )
                log.info("Сообщение отправлено без клавиатуры")
            except Exception as exc2:  # noqa: BLE001
                log.warning("Повторная отправка тоже не удалась: %s", exc2)

    async def _publish(
        self,
        game: Game,
        board: chess.Board,
        *,
        new_message: bool = False,
        reply_to: int | None = None,
        reply_chat: int | None = None,
    ) -> None:
        png = board_render.render_cached(board.fen(), game.last_move_uci)
        caption = texts.board_caption(game, board, self.username or "бот")
        buttons = keyboards.board_buttons(game, self.username or "")

        first_sent = False
        for chat_id, slot in game.targets():
            msg_id = game.msg_white if slot == "white" else game.msg_black
            if msg_id and not new_message:
                try:
                    await self.client.edit_message(
                        chat_id,
                        msg_id,
                        file=_photo(png),
                        text=caption,
                        parse_mode="html",
                        buttons=buttons,
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.info("Не смог обновить доску %s в %s: %s", msg_id, chat_id, exc)

            msg = await self.client.send_file(
                chat_id,
                file=_photo(png),
                caption=caption,
                parse_mode="html",
                buttons=buttons,
                reply_to=reply_to if (not first_sent and chat_id == reply_chat) else None,
            )
            if slot == "white":
                game.msg_white = msg.id
            else:
                game.msg_black = msg.id
            first_sent = True

        self.store.save(game)

    async def _clear_old_keyboard(self, game: Game | None) -> None:
        if not game or game.id is None:
            return
        for chat_id, slot in game.targets():
            msg_id = game.msg_white if slot == "white" else game.msg_black
            if not msg_id:
                continue
            try:
                await self.client.edit_message(
                    chat_id, msg_id, buttons=keyboards.empty_markup()
                )
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- сообщения
    async def on_message(self, event) -> None:
        if getattr(event, "out", False):
            return
        raw = (event.raw_text or "").strip()
        if not raw:
            return
        # в группе реагируем только на обращение к боту, но команды (/new и т.п.)
        # Telegram и так доставляет боту — их принимаем без упоминания
        if not event.is_private and not raw.startswith("/") and not await self._addressed(event):
            return
        try:
            sender = await event.get_sender()
        except Exception:  # noqa: BLE001
            sender = None
        if sender is not None and getattr(sender, "bot", False):
            return

        text = self._strip_mention(raw).strip()
        log.info(
            "Входящее: чат=%s user=%s private=%s текст=%r",
            event.chat_id,
            event.sender_id,
            event.is_private,
            text[:80],
        )
        if not text:
            await self._reply(
                event,
                "Напиши ход, например: "
                + texts.mention_hint(self.username or "бот", "пешка e4")
                + "\nИли /help — все команды.",
            )
            return

        if text.startswith("/"):
            command, _, rest = text[1:].partition(" ")
            command = command.split("@")[0].lower()
            rest = rest.strip()
            if command == "start":
                if rest.startswith("g_"):
                    await self._open_invite(event, rest[2:])
                else:
                    await self.cmd_start(event)
                return
            if command == "move":
                await self._handle_move(event, rest)
                return
            handler = {
                "start": self.cmd_start,
                "help": self.cmd_help,
                "new": self._new_game,
                "join": self._join_command,
                "board": self._show_board,
                "resign": self._offer_resign,
                "draw": self._offer_draw,
                "cancel": self._cancel_game,
                "menu": self._show_menu,
            }.get(command)
            if handler:
                await handler(event)
                return
            await self._reply(event, "🤔 Неизвестная команда. Список: /help")
            return

        action = self._match_keyword(text)
        if action == "new":
            await self._new_game(event)
        elif action == "join":
            await self._join_command(event)
        elif action == "resign":
            await self._offer_resign(event)
        elif action == "draw":
            await self._offer_draw(event)
        elif action == "board":
            await self._show_board(event)
        elif action == "help":
            await self.cmd_help(event)
        elif action == "cancel":
            await self._cancel_game(event)
        elif action == "menu":
            await self._show_menu(event)
        else:
            await self._handle_move(event, text)

    async def _addressed(self, event) -> bool:
        username = self.username
        text = (event.raw_text or "").lower()
        if username and f"@{username}" in text:
            return True
        if getattr(event, "is_reply", False):
            try:
                replied = await event.get_reply_message()
            except Exception:  # noqa: BLE001
                replied = None
            if replied is not None and replied.sender_id == getattr(self.me, "id", None):
                return True
        return False

    def _strip_mention(self, text: str) -> str:
        username = self.username
        if not username:
            return text
        return re.sub(r"@" + re.escape(username) + r"\b", " ", text, flags=re.IGNORECASE)

    @staticmethod
    def _match_keyword(text: str) -> str | None:
        low = " ".join(re.sub(r"[^\w\s-]", " ", notation.normalize(text)).split())
        for action, words in KEYWORDS.items():
            for word in words:
                if low == word or low.startswith(word + " "):
                    return action
        return None

    # ---------------------------------------------------------------- команды
    async def cmd_start(self, event) -> None:
        buttons = keyboards.main_menu() if event.is_private else keyboards.start_buttons()
        await self._notify(
            event.chat_id,
            texts.start_text(self.username or "бот"),
            buttons=buttons,
            reply_to=self._reply_to(event),
        )

    async def cmd_help(self, event) -> None:
        buttons = keyboards.main_menu() if event.is_private else None
        await self._notify(
            event.chat_id,
            texts.help_text(self.username or "бот"),
            buttons=buttons,
            reply_to=self._reply_to(event),
        )

    async def cb_help(self, event, rest: list[str]) -> None:
        buttons = keyboards.main_menu() if event.is_private else None
        await self._notify(event.chat_id, texts.help_text(self.username or "бот"), buttons=buttons)
        await self._answer(event)

    async def _show_menu(self, event) -> None:
        if event.is_private:
            await self._notify(
                event.chat_id,
                texts.menu_text(self.username or "бот"),
                buttons=keyboards.main_menu(),
                reply_to=self._reply_to(event),
            )
        else:
            await self._reply(event, texts.menu_group_text())

    # --------------------------------------------------------------- инлайн
    async def on_inline(self, event) -> None:
        """@бот в любом чате → предложить партию на 5/10/15 минут."""
        if not self.inline_secret:
            return
        query = (event.text or "").strip().lower()
        sender = await event.get_sender()
        name = display_name(sender)

        minutes_list = list(invites.ALLOWED_MINUTES)
        if query:
            exact = [m for m in invites.ALLOWED_MINUTES if str(m) == query]
            prefix = [m for m in invites.ALLOWED_MINUTES if str(m).startswith(query)]
            filtered = exact or prefix
            if filtered:
                minutes_list = filtered

        results = []
        for minutes in minutes_list:
            token = invites.make_token(self.inline_secret, event.sender_id, minutes)
            results.append(
                await event.builder.article(
                    id=f"chess-{minutes}",
                    title=f"♟ Шахматы · {minutes} мин",
                    description="Предложить партию в этот чат",
                    text=texts.inline_invite_text(name, minutes, self.username or "бот"),
                    buttons=[Button.inline("✅ Присоединиться", f"ij:{token}".encode())],
                )
            )
        await event.answer(results, cache_time=0, private=True)

    async def cb_ij(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        parsed = invites.parse_token(self.inline_secret, token)
        if parsed is None:
            await self._answer(
                event, "Приглашение устарело — набери @бот в чате заново", alert=True
            )
            return
        white_id, minutes = parsed

        if event.sender_id == white_id:
            await self._answer(event, "Это твоё приглашение — отправь его другу 🙂", alert=True)
            return
        if event.via_inline and event.is_private:
            await self._start_inline_board(event, white_id, minutes)
            return
        if event.is_private:
            await self._answer(event, "Открой приглашение и нажми кнопку в нём", alert=True)
            return

        chat_id = event.chat_id
        async with self._lock(self._chat_key(chat_id)):
            existing = self.store.current(chat_id)
            if existing and (existing.is_active or existing.is_waiting):
                await self._answer(event, "В этом чате уже есть партия", alert=True)
                return

            try:
                white = await self.client.get_entity(white_id)
                white_name = display_name(white)
            except Exception:  # noqa: BLE001
                white_name = "Игрок"
            sender = await event.get_sender()
            game = Game(
                mode=MODE_GROUP,
                white_id=white_id,
                white_name=white_name,
                black_id=event.sender_id,
                black_name=display_name(sender),
                white_chat=chat_id,
                black_chat=chat_id,
                status=STATUS_ACTIVE,
                base_ms=minutes * 60_000,
            )
            game.start_clock()
            self.store.save(game)
            try:
                await self._publish(game, game.board(), new_message=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Инлайн-партия в %s не началась: %s", chat_id, exc)
                self.store.delete(game.id)
                await self._answer(
                    event,
                    f"Не могу писать в этот чат. Добавь @{self.username} в чат и нажми ещё раз.",
                    alert=True,
                )
                return
            await self._notify(chat_id, texts.game_started_text(game))

        await self._answer(event, "Ты играешь чёрными ♟")

    # ---------------------------------------------------------- инлайн-доска
    @staticmethod
    def _inline_message_key(msg_id) -> bytes:
        return msg_id._bytes()

    async def _start_inline_board(self, event, white_id: int, minutes: int) -> None:
        """Партия прямо в инлайн-сообщении: без чата с ботом и без Start."""
        msg_id = event.query.msg_id
        key = self._inline_message_key(msg_id)
        existing = self._inline_by_message.get(key)
        if existing is not None:
            text = (
                "Партия уже окончена — набери @бот заново"
                if existing.game.is_finished
                else "Партия уже началась"
            )
            await self._answer(event, text, alert=True)
            return

        try:
            white = await self.client.get_entity(white_id)
            white_name = display_name(white)
        except Exception:  # noqa: BLE001
            white_name = "Игрок"
        sender = await event.get_sender()
        black_name = display_name(sender)

        game = Game(
            mode="inline",
            white_id=white_id,
            white_name=white_name,
            black_id=event.sender_id,
            black_name=black_name,
            white_chat=white_id,
            black_chat=event.sender_id,
            status=STATUS_ACTIVE,
            base_ms=minutes * 60_000,
        )
        game.start_clock()
        inline = InlineGame(secrets.token_urlsafe(6), msg_id, game)

        if not await self._edit_inline(inline):
            await self._answer(
                event,
                "Не удалось показать доску. Отправь приглашение ещё раз.",
                alert=True,
            )
            return

        self._inline_games[inline.token] = inline
        self._inline_by_message[key] = inline
        log.info(
            "Инлайн-партия %s↔%s (%s мин) началась",
            white_id,
            event.sender_id,
            minutes,
        )
        await self._answer(event, "Ты играешь чёрными ♟ Нажми свою фигуру.")

    async def _edit_inline(
        self,
        inline: InlineGame,
        *,
        controls: str = "board",
        buttons_only: bool = False,
    ) -> bool:
        game = inline.game
        board = game.board()
        if game.is_finished and inline.finished_at is None:
            inline.finished_at = time.time()
        if game.is_finished:
            buttons = keyboards.empty_markup()
        elif controls == "resign":
            buttons = keyboards.inline_resign_confirm(inline.token)
        elif controls == "draw":
            buttons = keyboards.inline_draw_confirm(inline.token)
        else:
            mover_id = game.white_id if board.turn == chess.WHITE else game.black_id
            selected = inline.selected.get(mover_id) if mover_id is not None else None
            buttons = keyboards.inline_board_buttons(inline.token, board, selected)

        try:
            if buttons_only:
                await self.client.edit_message(inline.msg_id, None, buttons=buttons)
                return True
        except Exception as exc:  # noqa: BLE001
            if not buttons_only:
                log.warning("Не удалось обновить инлайн-доску %s: %s", inline.token, exc)
                return False
            log.info("Кнопки инлайн-доски не обновились (%s), шлю доску целиком", exc)

        try:
            png = board_render.render_cached(board.fen(), game.last_move_uci)
            caption = texts.inline_board_caption(game, board, self.username or "бот")
            if inline.warning_text and not game.is_finished:
                caption += "\n\n" + inline.warning_text
            media = None
            try:
                media = await self._prepared_photo(png)
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "Предзагрузка фото для инлайна не удалась (%s), пробую напрямую",
                    exc,
                )
            await self.client.edit_message(
                inline.msg_id,
                None,
                file=media if media is not None else _photo(png),
                text=caption,
                parse_mode="html",
                buttons=buttons,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.info(
                "Картинку в инлайн-сообщении обновить не вышло (%s), шлю доску текстом",
                exc,
            )

        return await self._edit_inline_text(inline, buttons)

    async def _prepared_photo(self, png: bytes):
        """Загружает фото на сервер заранее: иначе инлайн-редактирование
        падает с FilePart0Missing."""
        # Имя с расширением обязательно: без него Telegram отвечает
        # «The extension of the photo is invalid» на UploadMediaRequest.
        handle = await self.client.upload_file(io.BytesIO(png), file_name="board.png")
        media = await self.client(
            functions.messages.UploadMediaRequest(
                types.InputPeerSelf(),
                types.InputMediaUploadedPhoto(handle),
            )
        )
        if media is None or getattr(media, "photo", None) is None:
            raise RuntimeError("сервер не вернул фото")
        return media

    async def _edit_inline_text(self, inline: InlineGame, buttons) -> bool:
        """Запасной вариант: текстовая доска, если картинка не редактируется."""
        try:
            board = inline.game.board()
            caption = texts.inline_board_caption(
                inline.game, board, self.username or "бот"
            )
            if inline.warning_text and not inline.game.is_finished:
                caption += "\n\n" + inline.warning_text
            await self.client.edit_message(
                inline.msg_id,
                None,
                text=caption + "\n\n" + texts.inline_text_board(board),
                parse_mode="html",
                buttons=buttons,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось обновить инлайн-доску %s: %s", inline.token, exc)
            return False

    async def cb_iq(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        square = rest[1].lower() if len(rest) > 1 else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(
                event,
                "Партия устарела — набери @бот и начни новую",
                alert=True,
            )
            return

        async with self._lock(f"inline:{token}"):
            game = inline.game
            if game.is_finished:
                await self._answer(
                    event, game.result_text or "Партия окончена", alert=True
                )
                return
            if event.sender_id not in (game.white_id, game.black_id):
                await self._answer(event, "Ты не участник этой партии", alert=True)
                return
            if game.flagged_color() is not None:
                await self._finish_inline_timeout(inline)
                await self._answer(event, "⌛ Время вышло", alert=True)
                return
            color = game.color_of_user(event.sender_id)
            board = game.board()
            if color != board.turn:
                await self._answer(event, "Сейчас ход соперника", alert=True)
                return
            try:
                target = chess.parse_square(square)
            except ValueError:
                await self._answer(event, "Кнопка повреждена", alert=True)
                return

            selected = inline.selected.get(event.sender_id)
            if selected is None:
                piece = board.piece_at(target)
                if piece is None or piece.color != color:
                    await self._answer(event, "Это не твоя фигура", alert=True)
                    return
                if not any(move.from_square == target for move in board.legal_moves):
                    await self._answer(event, "У этой фигуры нет ходов", alert=True)
                    return
                inline.selected[event.sender_id] = square
                await self._edit_inline(inline, buttons_only=True)
                await self._answer(
                    event, f"Выбрана {square}. Теперь клетка назначения."
                )
                return

            if square == selected:
                inline.selected.pop(event.sender_id, None)
                await self._edit_inline(inline, buttons_only=True)
                await self._answer(event, "Выбор снят")
                return

            target_piece = board.piece_at(target)
            if target_piece is not None and target_piece.color == color:
                inline.selected[event.sender_id] = square
                await self._edit_inline(inline, buttons_only=True)
                await self._answer(
                    event, f"Выбрана {square}. Теперь клетка назначения."
                )
                return

            uci = selected + square
            from_square = chess.parse_square(selected)
            moving = board.piece_at(from_square)
            if (
                moving is not None
                and moving.piece_type == chess.PAWN
                and chess.square_rank(target) in (0, 7)
            ):
                uci += "q"  # превращение по умолчанию — ферзь

            try:
                move = board.parse_uci(uci)
            except ValueError:
                inline.selected.pop(event.sender_id, None)
                destinations = sorted(
                    chess.square_name(mv.to_square)
                    for mv in board.legal_moves
                    if mv.from_square == from_square
                )
                hint = ", ".join(destinations) or "нет"
                await self._edit_inline(inline, buttons_only=True)
                await self._answer(
                    event, f"Так нельзя. Возможные ходы: {hint}", alert=True
                )
                return

            before = board.copy()
            game.spend_time(color)
            board.push(move)
            game.fen = board.fen()
            game.moves = (game.moves + " " + move.uci()).strip()
            game.last_move_uci = move.uci()
            game.last_move = notation.describe_move(before, move)
            inline.selected.clear()
            inline.draw_offer_by = None
            inline.resign_pending_by = None
            self._apply_game_over(game, board)
            await self._edit_inline(inline)
            if game.is_finished:
                await self._answer(event, "Партия окончена")
            else:
                await self._answer(event, "Ход сделан")

    async def cb_ic(self, event, rest: list[str]) -> None:
        """Снять выбор фигуры и вернуться к списку своих фигур."""
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        game = inline.game
        if game.is_finished:
            await self._answer(event, "Партия уже окончена", alert=True)
            return
        if event.sender_id not in (game.white_id, game.black_id):
            await self._answer(event, "Ты не участник этой партии", alert=True)
            return
        if game.color_of_user(event.sender_id) != game.board().turn:
            await self._answer(event, "Сейчас ход соперника", alert=True)
            return
        if inline.selected.pop(event.sender_id, None) is None:
            await self._answer(event, "Сначала выбери фигуру", alert=True)
            return
        await self._edit_inline(inline, buttons_only=True)
        await self._answer(event, "Выбери другую фигуру")

    async def cb_ir(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        game = inline.game
        if game.is_finished:
            await self._answer(event, "Партия уже окончена", alert=True)
            return
        if event.sender_id not in (game.white_id, game.black_id):
            await self._answer(event, "Ты не участник этой партии", alert=True)
            return
        inline.selected.clear()
        inline.resign_pending_by = event.sender_id
        await self._edit_inline(inline, controls="resign", buttons_only=True)
        await self._answer(event, "Подтверди сдачу")

    async def cb_iry(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        game = inline.game
        if game.is_finished:
            await self._answer(event, "Партия уже окончена", alert=True)
            return
        if inline.resign_pending_by != event.sender_id:
            await self._answer(event, "Это не твоя кнопка", alert=True)
            return
        color = game.color_of_user(event.sender_id) or chess.WHITE
        game.status = STATUS_FINISHED
        game.result_text = texts.result_resign(
            event.sender_id, game.name_of(color), not color, game
        )
        inline.resign_pending_by = None
        await self._edit_inline(inline)
        await self._answer(event, "Ты сдался")

    async def cb_irn(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        if (
            inline.resign_pending_by is not None
            and inline.resign_pending_by != event.sender_id
        ):
            await self._answer(event, "Это не твоя кнопка", alert=True)
            return
        inline.resign_pending_by = None
        await self._edit_inline(inline, controls="board", buttons_only=True)
        await self._answer(event, "Продолжаем")

    async def cb_id(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        game = inline.game
        if game.is_finished:
            await self._answer(event, "Партия уже окончена", alert=True)
            return
        if event.sender_id not in (game.white_id, game.black_id):
            await self._answer(event, "Ты не участник этой партии", alert=True)
            return
        if inline.draw_offer_by is not None:
            if inline.draw_offer_by == event.sender_id:
                await self._answer(event, "Ты уже предложил ничью", alert=True)
            else:
                await self._answer(
                    event, "Сначала ответь на предложение соперника", alert=True
                )
            return
        inline.selected.clear()
        inline.draw_offer_by = event.sender_id
        await self._edit_inline(inline, controls="draw", buttons_only=True)
        await self._answer(event, "Предложение ничьей отправлено")

    async def cb_idy(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        game = inline.game
        if game.is_finished:
            await self._answer(event, "Партия уже окончена", alert=True)
            return
        if inline.draw_offer_by is None:
            await self._answer(event, "Предложения ничьей нет", alert=True)
            return
        if inline.draw_offer_by == event.sender_id:
            await self._answer(
                event, "Ничью предлагал ты — ждём соперника", alert=True
            )
            return
        game.status = STATUS_FINISHED
        game.result_text = texts.result_draw_agreement()
        inline.draw_offer_by = None
        await self._edit_inline(inline)
        await self._answer(event, "Ничья")

    async def cb_idn(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        inline = self._inline_games.get(token)
        if inline is None:
            await self._answer(event, "Партия устарела", alert=True)
            return
        if inline.draw_offer_by is None:
            await self._answer(event, "Предложения ничьей нет", alert=True)
            return
        if inline.draw_offer_by == event.sender_id:
            await self._answer(event, "Отклонить может только соперник", alert=True)
            return
        inline.draw_offer_by = None
        await self._edit_inline(inline, controls="board", buttons_only=True)
        await self._answer(event, "Ничья отклонена")

    async def _finish_inline_timeout(self, inline: InlineGame) -> None:
        game = inline.game
        loser = game.board().turn
        game.status = STATUS_FINISHED
        game.set_stored_ms(loser, 0)
        game.result_text = texts.result_timeout(loser, game)
        inline.selected.clear()
        inline.draw_offer_by = None
        inline.resign_pending_by = None
        await self._edit_inline(inline)

    async def check_inline_clocks(self) -> None:
        """Проверка часов инлайн-партий (они живут в памяти)."""
        try:
            for inline in list(self._inline_games.values()):
                game = inline.game
                if not game.is_active or game.flagged_color() is None:
                    continue
                async with self._lock(f"inline:{inline.token}"):
                    if not game.is_active or game.flagged_color() is None:
                        continue
                    await self._finish_inline_timeout(inline)
                    log.info("Инлайн-партия %s: время вышло", inline.token)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в проверке часов инлайн-партий")

    # ------------------------------------------------------------ новая партия
    async def _new_game(self, event) -> None:
        chat_id = event.chat_id
        game = self.store.current(chat_id)
        if game and game.is_active:
            await self._reply(event, texts.already_active_text())
            return
        if game and game.is_waiting:
            await self._reply(event, texts.already_waiting_text())
            return

        sender = await event.get_sender()
        await self._ask_time(
            chat_id,
            event.sender_id,
            display_name(sender),
            reply_to=self._reply_to(event),
        )

    async def _ask_time(
        self,
        chat_id: int,
        user_id: int,
        name: str,
        *,
        reply_to: int | None = None,
    ) -> None:
        self._pending[chat_id] = (user_id, name)
        await self._notify(
            chat_id,
            texts.choose_time_text(),
            buttons=keyboards.time_buttons(),
            reply_to=reply_to,
        )

    async def cb_tc(self, event, rest: list[str]) -> None:
        try:
            minutes = int(rest[0])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        if minutes not in (5, 10, 15):
            await self._answer(event, "Такой контроль не поддерживается", alert=True)
            return

        chat_id = event.chat_id
        sender = await event.get_sender()
        name = display_name(sender)

        async with self._lock(self._chat_key(chat_id)):
            existing = self.store.current(chat_id)
            if existing and (existing.is_active or existing.is_waiting):
                await self._answer(event, "В этом чате уже есть партия", alert=True)
                return

            creator_id, creator_name = self._pending.get(chat_id, (event.sender_id, name))
            if event.sender_id != creator_id:
                await self._answer(
                    event, "Время выбирает тот, кто создал партию", alert=True
                )
                return
            self._pending.pop(chat_id, None)

            mode = MODE_INVITE if event.is_private else MODE_GROUP
            token = secrets.token_urlsafe(6) if mode == MODE_INVITE else None
            game = Game(
                mode=mode,
                white_id=creator_id,
                white_name=creator_name,
                white_chat=chat_id,
                black_chat=None if mode == MODE_INVITE else chat_id,
                status=STATUS_WAITING,
                base_ms=minutes * 60_000,
                invite_token=token,
            )
            self.store.save(game)

            await self._edit_prompt(
                event,
                f"⏱ Контроль времени: <b>{texts.time_control_name(minutes)}</b> каждому.",
            )
            await self._publish(game, game.board(), new_message=True)

        await self._answer(event, f"Партия создана: {minutes} мин")

    async def cb_tc_cancel(self, event, rest: list[str]) -> None:
        self._pending.pop(event.chat_id, None)
        await self._edit_prompt(event, "❌ Создание партии отменено.")
        await self._answer(event, "Отменено")

    # ------------------------------------------------------------- приглашения
    async def _open_invite(self, event, token: str) -> None:
        game = self.store.get_by_token(token)
        if not game or not game.is_waiting:
            await self._reply(event, texts.invite_invalid_text())
            return
        if event.sender_id == game.white_id:
            await self._reply(event, texts.invite_own_text())
            return
        current = self.store.current(event.chat_id)
        if current and current.id != game.id and (current.is_active or current.is_waiting):
            await self._reply(event, texts.invite_busy_text())
            return

        inviter = texts.user_link(game.white_id, game.white_name)
        await self._notify(
            event.chat_id,
            texts.invite_offer_text(inviter, game),
            buttons=keyboards.invite_offer_buttons(token),
            reply_to=self._reply_to(event),
        )

    async def cb_jac(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        game = self.store.get_by_token(token)
        if game is None:
            await self._answer(event, "Приглашение не найдено", alert=True)
            return

        async with self._lock(self._game_key(game.id)):
            game = self.store.get_by_token(token)
            if game is None or not game.is_waiting:
                await self._answer(event, "Приглашение уже неактуально", alert=True)
                return
            if event.sender_id == game.white_id:
                await self._answer(event, "Это твоё приглашение 🙂", alert=True)
                return
            current = self.store.current(event.chat_id)
            if current and current.id != game.id and (current.is_active or current.is_waiting):
                await self._answer(
                    event, "Сначала заверши свою текущую партию", alert=True
                )
                return

            sender = await event.get_sender()
            game.black_id = event.sender_id
            game.black_name = display_name(sender)
            game.black_chat = event.chat_id
            game.status = STATUS_ACTIVE
            game.start_clock()
            self.store.save(game)

            await self._publish(
                game,
                game.board(),
                reply_to=self._reply_to(event),
                reply_chat=event.chat_id,
            )
            await self._notify(
                game.chat_of(chess.WHITE),
                f"✅ {texts.user_link(game.black_id, game.black_name)} принял(а) приглашение.\n"
                "Твой ход — напиши, например: <code>пешка e4</code>",
            )

        await self._edit_prompt(event, "✅ Ты принял(а) приглашение. Играешь чёрными.")
        await self._answer(event, "Ты играешь чёрными ♟")

    async def cb_jdc(self, event, rest: list[str]) -> None:
        token = rest[0] if rest else ""
        game = self.store.get_by_token(token)
        if game is None or not game.is_waiting:
            await self._answer(event, "Приглашение уже неактуально", alert=True)
            return
        sender = await event.get_sender()
        await self._notify(
            game.chat_of(chess.WHITE),
            texts.invite_declined_text(display_name(sender)),
        )
        await self._edit_prompt(event, "🙅 Ты отклонил(а) приглашение.")
        await self._answer(event, "Приглашение отклонено")

    # ----------------------------------------------------------- группа: вход
    async def _join_command(self, event) -> None:
        if event.is_private:
            await self._reply(event, texts.join_private_text())
            return
        game = self.store.current(event.chat_id)
        if game is None:
            await self._reply(event, texts.not_started_text(self.username or "бот"))
            return
        if game.is_finished:
            await self._reply(event, texts.finished_text())
            return
        if game.is_active:
            await self._reply(event, "⚠️ Партия уже идёт. Дождись её окончания.")
            return
        if event.sender_id == game.white_id:
            await self._reply(event, "Ты уже создал(а) партию за белых. Ждём соперника 🙂")
            return
        await self._accept_group_join(event, game)

    async def cb_join(self, event, rest: list[str]) -> None:
        if event.is_private:
            await self._answer(event, texts.join_private_text(), alert=True)
            return
        try:
            game_id = int(rest[0])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        game = self.store.get(game_id)
        if game is None:
            await self._answer(event, "Партии уже нет", alert=True)
            return
        if not game.is_waiting:
            await self._answer(event, "Присоединиться уже нельзя", alert=True)
            return
        if event.sender_id == game.white_id:
            await self._answer(event, "Ты уже белый 🙂", alert=True)
            return
        await self._accept_group_join(event, game, via_button=True)

    async def _accept_group_join(self, event, game: Game, via_button: bool = False) -> None:
        async with self._lock(self._game_key(game.id)):
            game = self.store.get(game.id)
            if game is None or not game.is_waiting:
                if via_button:
                    await self._answer(event, "Присоединиться уже нельзя", alert=True)
                else:
                    await self._reply(event, texts.finished_text())
                return

            sender = await event.get_sender()
            game.black_id = event.sender_id
            game.black_name = display_name(sender)
            game.black_chat = event.chat_id
            game.status = STATUS_ACTIVE
            game.start_clock()
            self.store.save(game)
            await self._publish(game, game.board())
            started = game

        if via_button:
            await self._answer(event, "Ты играешь чёрными ♟")
        await self._notify(started.chat_of(chess.WHITE), texts.game_started_text(started))

    # ------------------------------------------------------------------ доска
    async def _show_board(self, event) -> None:
        game = self.store.current(event.chat_id)
        if game is None:
            await self._reply(event, texts.not_started_text(self.username or "бот"))
            return
        await self._publish(
            game,
            game.board(),
            new_message=True,
            reply_to=self._reply_to(event),
            reply_chat=event.chat_id,
        )

    async def cb_board(self, event, rest: list[str]) -> None:
        try:
            game_id = int(rest[0])
        except (IndexError, ValueError):
            game_id = None
        game = self.store.get(game_id) if game_id else self.store.current(event.chat_id)
        if game is None:
            await self._answer(event, "Партии нет", alert=True)
            return
        await self._publish(
            game,
            game.board(),
            new_message=True,
            reply_to=event.message_id,
            reply_chat=event.chat_id,
        )
        await self._answer(event, "Вот доска")

    async def _cancel_game(self, event) -> None:
        game = self.store.current(event.chat_id)
        if game is None:
            await self._reply(event, texts.not_started_text(self.username or "бот"))
            return
        if game.is_finished:
            await self._reply(event, texts.finished_text())
            return
        if event.sender_id not in (game.white_id, game.black_id):
            await self._reply(event, texts.not_player_text())
            return
        await self._abort(game, event.sender_id)

    async def cb_cancel(self, event, rest: list[str]) -> None:
        try:
            game_id = int(rest[0])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        game = self.store.get(game_id)
        if game is None:
            await self._answer(event, "Партии уже нет", alert=True)
            return
        if game.is_finished:
            await self._answer(event, "Партия уже окончена", alert=True)
            return
        if event.sender_id not in (game.white_id, game.black_id):
            await self._answer(event, "Отменить партию может только её участник", alert=True)
            return
        await self._abort(game, event.sender_id)
        await self._answer(event, "Партия отменена")

    async def _abort(self, game: Game, user_id: int) -> None:
        other: int | None = None
        async with self._lock(self._game_key(game.id)):
            game = self.store.get(game.id)
            if game is None or game.is_finished:
                return
            color = game.color_of_user(user_id) or chess.WHITE
            game.status = STATUS_FINISHED
            game.result_text = texts.result_aborted()
            self.store.save(game)
            await self._publish(game, game.board())
            if game.black_chat and game.black_chat != game.white_chat:
                other = game.opponent_chat(color)

        if other is not None:
            await self._notify(other, "❌ Соперник отменил партию.")

    # ------------------------------------------------------------------- ходы
    async def _handle_move(self, event, text: str) -> None:
        chat_id = event.chat_id
        user_id = event.sender_id

        game = self.store.current(chat_id)
        if game is None:
            await self._reply(event, texts.not_started_text(self.username or "бот"))
            return
        if game.is_finished:
            await self._reply(event, texts.finished_text())
            return
        if game.is_waiting:
            await self._reply(event, texts.waiting_text())
            return

        async with self._lock(self._game_key(game.id)):
            game = self.store.get(game.id)
            if game is None:
                await self._reply(event, texts.not_started_text(self.username or "бот"))
                return
            if game.is_finished:
                await self._reply(event, texts.finished_text())
                return
            if game.is_waiting:
                await self._reply(event, texts.waiting_text())
                return

            color = game.color_of_user(user_id)
            if color is None:
                await self._reply(event, texts.not_player_text())
                return

            if game.flagged_color() is not None:
                await self._finish_timeout(game)
                await self._reply(event, texts.result_timeout(game.board().turn, game))
                return

            board = game.board()
            if color != board.turn:
                await self._reply(event, texts.not_your_turn_text(board, game))
                return

            result = notation.resolve(board, text)
            if not result.ok:
                await self._reply(event, texts.move_error_text(result.error or "", result.data))
                return

            before = board.copy()
            game.spend_time(color)
            board.push(result.move)
            game.fen = board.fen()
            game.moves = (game.moves + " " + result.move.uci()).strip()
            game.last_move_uci = result.move.uci()
            game.last_move = notation.describe_move(before, result.move)
            self._apply_game_over(game, board)
            self.store.save(game)
            await self._publish(game, board)

            if game.is_finished and game.result_text:
                other = game.opponent_chat(color)
                if other != chat_id:
                    await self._notify(other, game.result_text)
            elif game.is_active:
                other = game.opponent_chat(color)
                if other != chat_id:
                    await self._notify(
                        other, texts.your_turn_text(game, game.last_move)
                    )

    def _apply_game_over(self, game: Game, board: chess.Board) -> None:
        if board.is_checkmate():
            game.status = STATUS_FINISHED
            game.result_text = texts.result_checkmate(board, game)
        elif board.is_stalemate():
            game.status = STATUS_FINISHED
            game.result_text = texts.result_stalemate()
        elif board.is_insufficient_material():
            game.status = STATUS_FINISHED
            game.result_text = texts.result_material()
        elif board.is_seventyfive_moves():
            game.status = STATUS_FINISHED
            game.result_text = texts.result_fifty()
        elif board.is_fivefold_repetition():
            game.status = STATUS_FINISHED
            game.result_text = texts.result_repetition()

    # ------------------------------------------------------------------- часы
    async def _finish_timeout(self, game: Game) -> None:
        board = game.board()
        loser = board.turn
        game.status = STATUS_FINISHED
        game.set_stored_ms(loser, 0)
        game.result_text = texts.result_timeout(loser, game)
        self.store.save(game)
        await self._publish(game, board)

    async def _watch_clocks(self) -> None:
        while True:
            await asyncio.sleep(CLOCK_TICK)
            await self.check_clocks()
            await self.check_time_warnings()
            await self.check_inline_clocks()
            await self.check_inline_time_warnings()
            if self.finished_ttl > 0 and time.time() - self._last_cleanup >= CLEANUP_TICK:
                self._last_cleanup = time.time()
                await self.cleanup_finished_games()
            # убираем из памяти пустые блокировки
            if len(self._locks) > 512:
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}

    @staticmethod
    def _pick_warning(remaining_ms: int, warned: set[int]) -> int | None:
        """Какой порог предупредить сейчас (без пачки сообщений после рестарта)."""
        crossed = [step for step in WARNING_STEPS if remaining_ms <= step * 1000]
        fresh = [step for step in crossed if step not in warned]
        if not fresh:
            return None
        threshold = min(fresh)
        # все уже пройденные пороги считаем закрытыми: не спамим 3/2/1 сразу
        warned.update(crossed)
        return threshold

    async def check_time_warnings(self, now: float | None = None) -> None:
        """Предупреждения о низком времени в обычных партиях."""
        try:
            for candidate in self.store.active_with_clock():
                async with self._lock(self._game_key(candidate.id)):
                    game = self.store.get(candidate.id)
                    if game is None or not game.is_active:
                        continue
                    for color in (chess.WHITE, chess.BLACK):
                        remaining = game.remaining_ms(color, now)
                        if remaining is None:
                            continue
                        warned = self._warned.setdefault((game.id, color), set())
                        threshold = self._pick_warning(remaining, warned)
                        if threshold is None:
                            continue
                        message = texts.time_warning_text(
                            game, color, threshold // 60
                        )
                        log.info(
                            "Партия %s: у %s осталось меньше %s мин",
                            game.id,
                            game.name_of(color),
                            threshold // 60,
                        )
                        for chat_id, _slot in game.targets():
                            await self._notify(chat_id, message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в предупреждениях о времени")

    async def check_inline_time_warnings(self, now: float | None = None) -> None:
        """Предупреждения о низком времени в инлайн-партиях (в подписи доски)."""
        try:
            for inline in list(self._inline_games.values()):
                if not inline.game.is_active:
                    continue
                async with self._lock(f"inline:{inline.token}"):
                    game = inline.game
                    if not game.is_active:
                        continue
                    changed = False
                    for color in (chess.WHITE, chess.BLACK):
                        remaining = game.remaining_ms(color, now)
                        if remaining is None:
                            continue
                        warned = inline.warned.setdefault(color, set())
                        threshold = self._pick_warning(remaining, warned)
                        if threshold is None:
                            continue
                        inline.warning_text = texts.time_warning_text(
                            game, color, threshold // 60
                        )
                        changed = True
                        log.info(
                            "Инлайн-партия %s: у %s осталось меньше %s мин",
                            inline.token,
                            game.name_of(color),
                            threshold // 60,
                        )
                    if changed:
                        await self._edit_inline(inline)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в предупреждениях о времени инлайн-партий")

    async def cleanup_finished_games(self, now: float | None = None) -> None:
        """Удаляет старые оконченные партии: сообщения с досками и записи в базе.

        Инлайн-сообщения Telegram удалить не даёт, поэтому у них убираются
        кнопки, а сама партия исчезает из памяти бота.
        """
        if self.finished_ttl <= 0:
            return
        now = now if now is not None else time.time()
        try:
            for game in self.store.finished_older_than(self.finished_ttl, now):
                for chat_id, slot in game.targets():
                    msg_id = game.msg_white if slot == "white" else game.msg_black
                    if not msg_id:
                        continue
                    try:
                        await self.client.delete_messages(chat_id, [msg_id])
                    except Exception as exc:  # noqa: BLE001
                        log.info(
                            "Не удалось удалить доску партии %s в %s: %s",
                            game.id,
                            chat_id,
                            exc,
                        )
                self.store.delete(game.id)
                for color in (chess.WHITE, chess.BLACK):
                    self._warned.pop((game.id, color), None)
                log.info("Оконченная партия %s удалена", game.id)

            for inline in list(self._inline_games.values()):
                game = inline.game
                if not game.is_finished:
                    continue
                if inline.finished_at is None:
                    inline.finished_at = now
                    continue
                if now - inline.finished_at < self.finished_ttl:
                    continue
                try:
                    await self.client.edit_message(
                        inline.msg_id, None, buttons=keyboards.empty_markup()
                    )
                except Exception as exc:  # noqa: BLE001
                    log.info(
                        "Не удалось убрать кнопки инлайн-партии %s: %s",
                        inline.token,
                        exc,
                    )
                self._inline_games.pop(inline.token, None)
                self._inline_by_message.pop(
                    self._inline_message_key(inline.msg_id), None
                )
                log.info("Оконченная инлайн-партия %s удалена", inline.token)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в очистке оконченных партий")

    async def check_clocks(self) -> None:
        """Один проход проверки времени (отдельно — чтобы можно было тестировать)."""
        try:
            for candidate in self.store.active_with_clock():
                if candidate.flagged_color() is None:
                    continue
                async with self._lock(self._game_key(candidate.id)):
                    game = self.store.get(candidate.id)
                    if game is None or not game.is_active:
                        continue
                    loser = game.flagged_color()
                    if loser is None:
                        continue
                    board = game.board()
                    game.status = STATUS_FINISHED
                    game.set_stored_ms(loser, 0)
                    game.result_text = texts.result_timeout(loser, game)
                    self.store.save(game)
                    await self._publish(game, board)

                    message = (
                        f"⌛ <b>Время вышло</b> у "
                        f"{texts.user_link(game.user_of(loser), game.name_of(loser))}."
                    )
                    for chat_id, _slot in game.targets():
                        await self._notify(chat_id, message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в проверке часов")

    # ------------------------------------------------------------ сдача/ничья
    async def _offer_resign(self, event) -> None:
        game = self.store.current(event.chat_id)
        if game is None:
            await self._reply(event, texts.not_started_text(self.username or "бот"))
            return
        await self._resign_prompt(event, game)

    async def cb_resign_ask(self, event, rest: list[str]) -> None:
        try:
            game_id = int(rest[0])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        game = self.store.get(game_id)
        if game is None:
            await self._answer(event, "Партии уже нет", alert=True)
            return
        await self._resign_prompt(event, game, via_button=True)

    async def _resign_prompt(self, event, game: Game, via_button: bool = False) -> None:
        if game.is_finished:
            if via_button:
                await self._answer(event, "Партия уже окончена", alert=True)
            else:
                await self._reply(event, texts.finished_text())
            return
        if event.sender_id not in (game.white_id, game.black_id):
            if via_button:
                await self._answer(event, "Ты не играешь в этой партии", alert=True)
            else:
                await self._reply(event, texts.not_player_text())
            return

        if game.is_waiting:
            if event.sender_id == game.white_id:
                await self._abort(game, event.sender_id)
                if via_button:
                    await self._answer(event, "Партия отменена")
            elif via_button:
                await self._answer(event, texts.waiting_text(), alert=True)
            else:
                await self._reply(event, texts.waiting_text())
            return

        sender = await event.get_sender()
        await self._notify(
            event.chat_id,
            texts.resign_confirm_text(texts.user_link(event.sender_id, display_name(sender))),
            buttons=keyboards.resign_buttons(game.id, event.sender_id),
            reply_to=event.message_id if via_button else self._reply_to(event),
        )
        if via_button:
            await self._answer(event)

    async def cb_resign_ok(self, event, rest: list[str]) -> None:
        try:
            game_id, target = int(rest[0]), int(rest[1])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        if event.sender_id != target:
            await self._answer(event, texts.not_your_confirmation(), alert=True)
            return

        async with self._lock(self._game_key(game_id)):
            game = self.store.get(game_id)
            if game is None or not game.is_active:
                await self._answer(event, "Партия уже не идёт", alert=True)
                return
            color = game.color_of_user(event.sender_id)
            if color is None:
                await self._answer(event, "Ты не играешь в этой партии", alert=True)
                return

            sender = await event.get_sender()
            game.status = STATUS_FINISHED
            game.result_text = texts.result_resign(
                event.sender_id, display_name(sender), not color, game
            )
            self.store.save(game)
            await self._publish(game, game.board())
            other = game.opponent_chat(color)

        await self._edit_prompt(
            event, f"🏳️ {texts.user_link(event.sender_id, display_name(sender))} сдался(ась)."
        )
        if other != event.chat_id:
            await self._notify(other, "🏳️ Соперник сдался. Партия окончена.")
        await self._answer(event, "Партия окончена")

    async def cb_resign_no(self, event, rest: list[str]) -> None:
        try:
            game_id, target = int(rest[0]), int(rest[1])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        if event.sender_id != target:
            await self._answer(event, texts.not_your_confirmation(), alert=True)
            return
        sender = await event.get_sender()
        await self._edit_prompt(
            event, texts.resign_cancelled_text(texts.user_link(event.sender_id, display_name(sender)))
        )
        await self._answer(event, "Продолжаем игру 👍")

    async def _offer_draw(self, event) -> None:
        game = self.store.current(event.chat_id)
        if game is None:
            await self._reply(event, texts.not_started_text(self.username or "бот"))
            return
        await self._draw_prompt(event, game)

    async def cb_draw_ask(self, event, rest: list[str]) -> None:
        try:
            game_id = int(rest[0])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        game = self.store.get(game_id)
        if game is None:
            await self._answer(event, "Партии уже нет", alert=True)
            return
        await self._draw_prompt(event, game, via_button=True)

    async def _draw_prompt(self, event, game: Game, via_button: bool = False) -> None:
        if game.is_finished:
            if via_button:
                await self._answer(event, "Партия уже окончена", alert=True)
            else:
                await self._reply(event, texts.finished_text())
            return
        if game.is_waiting:
            if via_button:
                await self._answer(event, texts.waiting_text(), alert=True)
            else:
                await self._reply(event, texts.waiting_text())
            return
        color = game.color_of_user(event.sender_id)
        if color is None:
            if via_button:
                await self._answer(event, "Ты не играешь в этой партии", alert=True)
            else:
                await self._reply(event, texts.not_player_text())
            return

        sender = await event.get_sender()
        link = texts.user_link(event.sender_id, display_name(sender))
        target_chat = game.opponent_chat(color)
        await self._notify(
            target_chat,
            texts.draw_offer_text(link),
            buttons=keyboards.draw_buttons(game.id, event.sender_id),
            reply_to=self._reply_to(event) if target_chat == event.chat_id else None,
        )
        if target_chat != event.chat_id:
            await self._notify(event.chat_id, "🤝 Предложение ничьей отправлено сопернику.")
        if via_button:
            await self._answer(event, "Предложение отправлено")

    async def cb_draw_yes(self, event, rest: list[str]) -> None:
        try:
            game_id, offerer = int(rest[0]), int(rest[1])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return

        async with self._lock(self._game_key(game_id)):
            game = self.store.get(game_id)
            if game is None or not game.is_active:
                await self._answer(event, "Партия уже не идёт", alert=True)
                return
            if event.sender_id == offerer:
                await self._answer(event, "Ничью должен подтвердить соперник", alert=True)
                return
            color = game.color_of_user(event.sender_id)
            if color is None:
                await self._answer(event, "Ты не играешь в этой партии", alert=True)
                return

            game.status = STATUS_FINISHED
            game.result_text = texts.result_draw_agreement()
            self.store.save(game)
            await self._publish(game, game.board())
            other = game.opponent_chat(color)

        await self._edit_prompt(event, "🤝 Ничья по договорённости. Партия окончена.")
        if other != event.chat_id:
            await self._notify(other, "🤝 Соперник согласился на ничью. Партия окончена.")
        await self._answer(event, "Ничья 🤝")

    async def cb_draw_no(self, event, rest: list[str]) -> None:
        try:
            game_id, offerer = int(rest[0]), int(rest[1])
        except (IndexError, ValueError):
            await self._answer(event, "Кнопка устарела", alert=True)
            return
        game = self.store.get(game_id)
        if game is None or not game.is_active:
            await self._answer(event, "Партия уже не идёт", alert=True)
            return
        sender = await event.get_sender()
        link = texts.user_link(event.sender_id, display_name(sender))
        if event.sender_id == offerer:
            await self._edit_prompt(event, texts.draw_cancelled_text(link))
            await self._answer(event, "Предложение отозвано")
        else:
            if event.sender_id not in (game.white_id, game.black_id):
                await self._answer(event, "Ты не играешь в этой партии", alert=True)
                return
            await self._edit_prompt(event, texts.draw_declined_text(link))
            await self._answer(event, "Играем дальше")
            other = game.opponent_chat(game.color_of_user(event.sender_id))
            if other != event.chat_id:
                await self._notify(other, texts.draw_declined_text(link))

    # -------------------------------------------------------------- служебное
    async def on_callback(self, event) -> None:
        raw = event.data or b""
        try:
            data = raw.decode("utf-8")
        except UnicodeDecodeError:
            await self._answer(event, "Кнопка повреждена")
            return
        parts = data.split(":")
        action = parts[0]
        rest = parts[1:]
        handler = getattr(self, f"cb_{action}", None)
        if handler is None:
            await self._answer(event)
            return
        try:
            await handler(event, rest)
        except Exception:  # noqa: BLE001
            log.exception("Кнопка %s упала", data)
            await self._answer(event, "Что-то пошло не так, попробуй ещё раз", alert=True)

    @staticmethod
    async def _answer(event, text: str | None = None, alert: bool = False) -> None:
        try:
            await event.answer(text, alert=alert)
        except Exception:  # noqa: BLE001
            log.debug("callback answer failed")

    async def _edit_prompt(self, event, text: str) -> None:
        try:
            await self.client.edit_message(
                event.chat_id,
                event.message_id,
                text=text,
                parse_mode="html",
                buttons=keyboards.empty_markup(),
            )
        except Exception as exc:  # noqa: BLE001
            log.info("Не удалось отредактировать сообщение: %s", exc)

    async def cb_new(self, event, rest: list[str]) -> None:
        game = self.store.current(event.chat_id)
        if game and game.is_active:
            await self._answer(event, "Сначала заверши текущую партию", alert=True)
            return
        if game and game.is_waiting:
            await self._answer(event, "Партия уже создана, ждём соперника", alert=True)
            return
        sender = await event.get_sender()
        await self._ask_time(event.chat_id, event.sender_id, display_name(sender))
        await self._answer(event, "Выбери время")


def register(
    client, store: Store, inline_secret: bytes = b"", finished_ttl: float = 600.0
) -> ChessBot:
    bot = ChessBot(
        client, store, inline_secret=inline_secret, finished_ttl=finished_ttl
    )
    client.add_event_handler(bot.on_message, events.NewMessage())
    client.add_event_handler(bot.on_callback, events.CallbackQuery())
    client.add_event_handler(bot.on_inline, events.InlineQuery())
    return bot
