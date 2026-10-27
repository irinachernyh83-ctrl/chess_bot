"""Отрисовка шахматной доски в PNG (в стиле прикреплённого референса).

Доска 8x8, координаты a-h по нижнему ряду и 1-8 по левому столбцу,
зелёные/кремовые клетки, тёмная рамка, фигуры-силуэты.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache

import chess
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- оформление
SQUARE = 96
BORDER = 10

LIGHT = (240, 238, 212)
DARK = (125, 163, 79)
BORDER_COLOR = (32, 32, 32)
BOARD_EDGE = (60, 72, 40)

LAST_MOVE_TINT = (246, 238, 120)
CHECK_TINT = (214, 72, 72)
HINT_ALPHA = 95

WHITE_FILL = (252, 251, 244)
WHITE_STROKE = (42, 42, 42)
BLACK_FILL = (58, 58, 58)
BLACK_STROKE = (22, 22, 22)

PIECE_FONT_SIZE = 84
COORD_FONT_SIZE = 22

_FILLED_GLYPHS = {
    chess.PAWN: "\u265f",
    chess.KNIGHT: "\u265e",
    chess.BISHOP: "\u265d",
    chess.ROOK: "\u265c",
    chess.QUEEN: "\u265b",
    chess.KING: "\u265a",
}

_FONT_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "DejaVuSans.ttf"),
    "C:/Windows/Fonts/seguisym.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

_font_path: str | None = None


def _find_font() -> str:
    global _font_path
    if _font_path and os.path.exists(_font_path):
        return _font_path
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            _font_path = path
            return path
    raise RuntimeError(
        "Не найден шрифт с шахматными символами. Положи DejaVuSans.ttf в папку assets/."
    )


@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_find_font(), size)


# ---------------------------------------------------------------- рендеринг
def render_png(board: chess.Board, last_move: chess.Move | None = None, flipped: bool = False) -> bytes:
    """Возвращает PNG-доску для текущей позиции."""
    size = SQUARE * 8
    total = size + BORDER * 2
    img = Image.new("RGB", (total, total), BORDER_COLOR)
    draw = ImageDraw.Draw(img, "RGBA")

    check_square = None
    if board.is_check():
        king = board.king(board.turn)
        if king is not None:
            check_square = king

    for rank in range(8):          # 0 = верх картинки
        for file in range(8):      # 0 = левая сторона
            square = chess.square(file, 7 - rank)
            if flipped:
                square = chess.square(7 - file, rank)

            x = BORDER + file * SQUARE
            y = BORDER + rank * SQUARE
            is_light = (chess.square_file(square) + chess.square_rank(square)) % 2 == 1
            draw.rectangle([x, y, x + SQUARE - 1, y + SQUARE - 1], fill=LIGHT if is_light else DARK)

            if last_move and square in (last_move.from_square, last_move.to_square):
                draw.rectangle([x, y, x + SQUARE - 1, y + SQUARE - 1], fill=LAST_MOVE_TINT + (HINT_ALPHA,))
            if check_square is not None and square == check_square:
                draw.rectangle([x, y, x + SQUARE - 1, y + SQUARE - 1], fill=CHECK_TINT + (HINT_ALPHA,))

            # координаты: цифры слева, буквы снизу — как на референсе
            if file == 0:
                draw.text(
                    (x + 6, y + 4),
                    str(chess.square_rank(square) + 1),
                    font=_font(COORD_FONT_SIZE),
                    fill=(58, 70, 36) if is_light else (236, 240, 220),
                )
            if rank == 7:
                draw.text(
                    (x + 7, y + SQUARE - 11),
                    chess.square_name(square)[0],
                    font=_font(COORD_FONT_SIZE),
                    anchor="ld",
                    fill=(58, 70, 36) if is_light else (236, 240, 220),
                )

            piece = board.piece_at(square)
            if piece is None:
                continue
            glyph = _FILLED_GLYPHS[piece.piece_type]
            if piece.color == chess.WHITE:
                fill, stroke, stroke_w = WHITE_FILL, WHITE_STROKE, 4
            else:
                fill, stroke, stroke_w = BLACK_FILL, BLACK_STROKE, 2

            draw.text(
                (x + SQUARE / 2, y + SQUARE / 2 + 2),
                glyph,
                font=_font(PIECE_FONT_SIZE),
                fill=fill,
                anchor="mm",
                stroke_width=stroke_w,
                stroke_fill=stroke,
            )

    draw.rectangle([BORDER - 1, BORDER - 1, total - BORDER, total - BORDER], outline=BOARD_EDGE, width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


@lru_cache(maxsize=48)
def render_cached(fen: str, last_move_uci: str | None = None, flipped: bool = False) -> bytes:
    board = chess.Board(fen)
    move = chess.Move.from_uci(last_move_uci) if last_move_uci else None
    return render_png(board, move, flipped)


if __name__ == "__main__":  # быстрый визуальный тест: python -m chessbot.board
    os.makedirs("tests/out", exist_ok=True)
    b = chess.Board()
    with open("tests/out/start.png", "wb") as f:
        f.write(render_png(b))
    b.push_uci("e2e4")
    b.push_uci("e7e5")
    b.push_uci("g1f3")
    with open("tests/out/after.png", "wb") as f:
        f.write(render_png(b, b.peek()))
    print("OK: tests/out/start.png, tests/out/after.png")
