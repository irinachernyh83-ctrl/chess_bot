"""Инлайн-кнопки и постоянное меню (Telethon)."""

from __future__ import annotations

import chess
from telethon import Button
from telethon.tl import types
from telethon.tl.types import ReplyInlineMarkup

from . import texts


def main_menu() -> types.ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода (только в личке)."""
    rows = [
        types.KeyboardButtonRow(
            [types.KeyboardButton(text="♟ Новая партия"), types.KeyboardButton(text="📋 Доска")]
        ),
        types.KeyboardButtonRow(
            [types.KeyboardButton(text="🤝 Ничья"), types.KeyboardButton(text="🏳️ Сдаться")]
        ),
        types.KeyboardButtonRow(
            [types.KeyboardButton(text="❌ Отменить"), types.KeyboardButton(text="❓ Помощь")]
        ),
    ]
    return types.ReplyKeyboardMarkup(
        rows=rows,
        resize=True,
        persistent=True,
        placeholder="Выбери действие или напиши ход",
    )


def time_buttons() -> list[list[Button]]:
    return [
        [
            Button.inline("⏱ 5 мин", b"tc:5"),
            Button.inline("⏱ 10 мин", b"tc:10"),
            Button.inline("⏱ 15 мин", b"tc:15"),
        ],
        [Button.inline("❌ Отмена", b"tc_cancel")],
    ]


def start_buttons() -> list[list[Button]]:
    return [
        [Button.inline("♟ Начать партию", b"new")],
        [Button.inline("❓ Как играть", b"help")],
    ]


def board_buttons(game, bot_username: str = "") -> list[list[Button]] | None:
    if game.id is None:
        return None

    if game.status == "waiting":
        if game.is_invite:
            invite = texts.invite_link(bot_username, game.invite_token)
            rows: list[list[Button]] = []
            if bot_username and game.invite_token:
                rows.append(
                    [Button.url("📤 Поделиться приглашением", texts.share_link(bot_username, game.invite_token))]
                )
                rows.append([Button.url("🔗 Открыть приглашение", invite)])
            rows.append([Button.inline("❌ Отменить", f"cancel:{game.id}".encode())])
            return rows
        return [
            [Button.inline("✅ Присоединиться", f"join:{game.id}".encode())],
            [Button.inline("❌ Отменить партию", f"cancel:{game.id}".encode())],
        ]

    if game.status == "active":
        return [
            [
                Button.inline("🤝 Ничья", f"draw_ask:{game.id}".encode()),
                Button.inline("🏳️ Сдаться", f"resign_ask:{game.id}".encode()),
            ],
            [Button.inline("📋 Показать доску", f"board:{game.id}".encode())],
        ]

    if game.status == "finished":
        return [[Button.inline("🆕 Новая партия", b"new")]]

    return None


def invite_offer_buttons(token: str) -> list[list[Button]]:
    return [
        [Button.inline("✅ Принять", f"jac:{token}".encode())],
        [Button.inline("🙅 Отказаться", f"jdc:{token}".encode())],
    ]


def resign_buttons(game_id: int, user_id: int) -> list[list[Button]]:
    return [
        [Button.inline("✅ Да, сдаться", f"resign_ok:{game_id}:{user_id}".encode())],
        [Button.inline("↩️ Нет, играем", f"resign_no:{game_id}:{user_id}".encode())],
    ]


def draw_buttons(game_id: int, offerer_id: int) -> list[list[Button]]:
    return [
        [Button.inline("🤝 Принять ничью", f"draw_yes:{game_id}:{offerer_id}".encode())],
        [Button.inline("🙅 Отклонить", f"draw_no:{game_id}:{offerer_id}".encode())],
    ]


_PIECE_ORDER = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 2,
    chess.ROOK: 3,
    chess.QUEEN: 4,
    chess.KING: 5,
}


def inline_board_buttons(
    token: str, board: chess.Board, selected: str | None = None
) -> list[list[Button]]:
    """Кнопки для игры прямо в инлайн-сообщении.

    Без выбора — только фигуры, у которых есть ходы, с подписью «e2 пешка».
    С выбором — только допустимые клетки назначения и кнопка «Назад».
    """
    rows: list[list[Button]] = []

    if selected:
        from_square = chess.parse_square(selected)
        targets: dict[int, list[chess.Move]] = {}
        for move in board.legal_moves:
            if move.from_square == from_square:
                targets.setdefault(move.to_square, []).append(move)

        buttons: list[Button] = []
        for target in sorted(
            targets, key=lambda sq: (chess.square_rank(sq), chess.square_file(sq))
        ):
            moves = targets[target]
            square_name = chess.square_name(target)
            label = square_name
            if any(board.is_capture(move) for move in moves):
                label += " ×"
            if any(move.promotion for move in moves):
                label += " ♛"
            buttons.append(Button.inline(label, f"iq:{token}:{square_name}".encode()))
        for i in range(0, len(buttons), 4):
            rows.append(buttons[i : i + 4])
        rows.append([Button.inline(f"↩️ Назад ({selected})", f"ic:{token}".encode())])
    else:
        sources = {move.from_square for move in board.legal_moves}

        def source_key(square: int) -> tuple[int, int, int]:
            piece = board.piece_at(square)
            kind = _PIECE_ORDER.get(piece.piece_type if piece else None, 9)
            return kind, chess.square_rank(square), chess.square_file(square)

        buttons = []
        for square in sorted(sources, key=source_key):
            piece = board.piece_at(square)
            name = texts.PIECE_RU.get(piece.piece_type, "фигура") if piece else "фигура"
            square_name = chess.square_name(square)
            buttons.append(
                Button.inline(
                    f"{square_name} {name}", f"iq:{token}:{square_name}".encode()
                )
            )
        for i in range(0, len(buttons), 2):
            rows.append(buttons[i : i + 2])

    rows.append(
        [
            Button.inline("🏳️ Сдаться", f"ir:{token}".encode()),
            Button.inline("🤝 Ничья", f"id:{token}".encode()),
        ]
    )
    return rows


def inline_resign_confirm(token: str) -> list[list[Button]]:
    return [
        [
            Button.inline("✅ Да, сдаюсь", f"iry:{token}".encode()),
            Button.inline("↩️ Нет, играем", f"irn:{token}".encode()),
        ]
    ]


def inline_draw_confirm(token: str) -> list[list[Button]]:
    return [
        [
            Button.inline("✅ Принять", f"idy:{token}".encode()),
            Button.inline("❌ Отклонить", f"idn:{token}".encode()),
        ]
    ]


def empty_markup() -> ReplyInlineMarkup:
    """Пустая инлайн-клавиатура — убирает кнопки у сообщения."""
    return ReplyInlineMarkup(rows=[])
