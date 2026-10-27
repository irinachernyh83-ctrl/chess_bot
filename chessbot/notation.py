"""Разбор ходов, написанных обычным текстом.

Поддерживаются форматы:
    пешка e4
    пешка e2 e4
    конь g1 f3
    конь f3
    ладья a1 a8
    слон c1 g5
    ферзь d1 h5
    король e1 g1
    e2 e4
    e2-e4
    e4
    рокировка / короткая рокировка / 0-0 / 0-0-0
    пешка e7 e8 ферзь   (превращение)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import chess

# ----------------------------------------------------------------- названия
PIECE_RU = {
    chess.PAWN: "пешка",
    chess.KNIGHT: "конь",
    chess.BISHOP: "слон",
    chess.ROOK: "ладья",
    chess.QUEEN: "ферзь",
    chess.KING: "король",
}
PIECE_RU_ACC = {
    chess.PAWN: "пешку",
    chess.KNIGHT: "коня",
    chess.BISHOP: "слона",
    chess.ROOK: "ладью",
    chess.QUEEN: "ферзя",
    chess.KING: "короля",
}

# короткие обозначения: только точное совпадение
_EXACT_ALIASES = {
    "п": chess.PAWN,
    "p": chess.PAWN,
    "к": chess.KNIGHT,
    "n": chess.KNIGHT,
    "с": chess.BISHOP,
    "b": chess.BISHOP,
    "л": chess.ROOK,
    "r": chess.ROOK,
    "ф": chess.QUEEN,
    "q": chess.QUEEN,
    "кр": chess.KING,
    "kr": chess.KING,
    "k": chess.KING,
}

# префиксы слов (порядок важен: "королев" раньше "корол")
_STEMS = (
    ("королев", chess.QUEEN),
    ("корол", chess.KING),
    ("ферз", chess.QUEEN),
    ("лад", chess.ROOK),
    ("тур", chess.ROOK),
    ("слон", chess.BISHOP),
    ("офицер", chess.BISHOP),
    ("лошад", chess.KNIGHT),
    ("кон", chess.KNIGHT),
    ("пеш", chess.PAWN),
    ("pawn", chess.PAWN),
    ("knight", chess.KNIGHT),
    ("horse", chess.KNIGHT),
    ("bishop", chess.BISHOP),
    ("rook", chess.ROOK),
    ("queen", chess.QUEEN),
    ("king", chess.KING),
)

_CASTLE_LONG = re.compile(r"(0-0-0|o-o-o|\bдлинн\w*\s+рокировк\w*|рокировк\w*\s+в\s+длинн)")
_CASTLE_SHORT = re.compile(r"(0-0|o-o|\bкоротк\w*\s+рокировк\w*|рокировк\w*\s+в\s+коротк)")
_CASTLE_ANY = re.compile(r"рокировк")

_SQUARE_RE = re.compile(r"([a-h][1-8])")
_CYR_FILES = {"а": "a", "в": "b", "с": "c", "е": "e"}
_PROMO_RE = re.compile(r"=\s*([nbrqклсф])")


# ------------------------------------------------------------------ парсинг
def normalize(text: str) -> str:
    """Приводит текст к нижнему регистру, чинит кириллические буквы-двойники."""
    t = (text or "").lower().replace("ё", "е")
    t = t.replace("–", "-").replace("—", "-").replace("−", "-")

    def _fix(m: re.Match[str]) -> str:
        return _CYR_FILES[m.group(1)]

    t = re.sub(r"(?<![а-я])([авес])(?=[1-8])", _fix, t)
    return t


def detect_piece(tokens: list[str]) -> int | None:
    """Ищет название фигуры среди токенов (первое найденное)."""
    for tok in tokens:
        tok = tok.strip(".,!?;:()[]«»\"'")
        if not tok:
            continue
        if tok in _EXACT_ALIASES:
            return _EXACT_ALIASES[tok]
        for stem, piece in _STEMS:
            if tok.startswith(stem):
                return piece
    return None


def detect_promotion(text: str, pieces: list[int]) -> int | None:
    m = _PROMO_RE.search(text)
    if m:
        ch = m.group(1)
        return _EXACT_ALIASES.get(ch)
    if len(pieces) >= 2 and pieces[0] == chess.PAWN:
        second = pieces[1]
        if second in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            return second
    return None


@dataclass
class Resolution:
    """Результат разбора: либо ход, либо код ошибки с деталями."""

    move: chess.Move | None = None
    error: str | None = None
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.move is not None


def _ok(move: chess.Move) -> Resolution:
    return Resolution(move=move)


def _err(code: str, **data) -> Resolution:
    return Resolution(error=code, data=data)


def _piece_matches(board: chess.Board, move: chess.Move, piece: int | None) -> bool:
    if piece is None:
        return True
    return board.piece_type_at(move.from_square) == piece


def _legal_targets(board: chess.Board, from_square: int) -> list[str]:
    return sorted({chess.square_name(m.to_square) for m in board.legal_moves if m.from_square == from_square})


def resolve(board: chess.Board, text: str) -> Resolution:
    """Превращает текст в легальный ход для текущей позиции (или ошибку)."""
    norm = normalize(text)
    tokens = re.split(r"[\s,.;:!?()\[\]«»\"']+", norm)

    # --- рокировка
    if _CASTLE_LONG.search(norm):
        return _castle(board, queenside=True)
    if _CASTLE_SHORT.search(norm):
        return _castle(board, queenside=False)
    if _CASTLE_ANY.search(norm):
        return _castle(board, queenside=False, auto=True)

    squares = [chess.parse_square(s) for s in _SQUARE_RE.findall(norm)]
    pieces = []
    for tok in tokens:
        p = detect_piece([tok])
        if p is not None and (not pieces or pieces[-1] != p):
            pieces.append(p)

    promotion = detect_promotion(norm, pieces)
    piece = pieces[0] if pieces else None

    if not squares:
        return _err("no_square")

    if len(squares) > 2:
        return _err("too_many_squares", squares=[chess.square_name(s) for s in squares[:4]])

    from_sq = squares[0] if len(squares) == 2 else None
    to_sq = squares[-1]

    # "e7 e8 ферзь" — фигура названа только для превращения
    if (
        promotion is None
        and len(pieces) == 1
        and pieces[0] in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)
        and from_sq is not None
        and chess.square_rank(to_sq) in (0, 7)
        and board.piece_type_at(from_sq) == chess.PAWN
    ):
        piece, promotion = chess.PAWN, pieces[0]

    candidates = [
        m
        for m in board.legal_moves
        if (from_sq is None or m.from_square == from_sq)
        and m.to_square == to_sq
        and _piece_matches(board, m, piece)
        and _promotion_ok(m, promotion)
    ]

    # без указания фигуры одно поле трактуем как ход пешкой (как в шахматной записи)
    if not candidates and from_sq is None and piece is None:
        pawn_moves = [
            m
            for m in board.legal_moves
            if m.to_square == to_sq and board.piece_type_at(m.from_square) == chess.PAWN and _promotion_ok(m, promotion)
        ]
        if pawn_moves:
            candidates = pawn_moves

    if not candidates:
        return _diagnose(board, from_sq, to_sq, piece)

    if len(candidates) > 1:
        options = [
            f"{PIECE_RU[board.piece_type_at(m.from_square)]} {chess.square_name(m.from_square)} {chess.square_name(m.to_square)}"
            for m in candidates
        ]
        return _err("ambiguous", options=options)

    return _ok(candidates[0])


def _promotion_ok(move: chess.Move, promotion: int | None) -> bool:
    if not move.promotion:
        return True
    if promotion is None:
        return move.promotion == chess.QUEEN  # по умолчанию — ферзь
    return move.promotion == promotion


def _castle(board: chess.Board, queenside: bool, auto: bool = False) -> Resolution:
    color = board.turn
    if auto:
        # просто "рокировка": берём ту, что сейчас возможна
        for side in (False, True):
            move = _castle_move(board, side)
            if move:
                return _ok(move)
        return _err("castle_impossible", reason="rights")

    move = _castle_move(board, queenside)
    if move:
        return _ok(move)

    if board.is_check():
        return _err("castle_impossible", reason="check")
    if queenside:
        has_rights = board.has_queenside_castling_rights(color)
    else:
        has_rights = board.has_kingside_castling_rights(color)
    return _err("castle_impossible", reason="rights" if not has_rights else "attacked")


def _castle_move(board: chess.Board, queenside: bool) -> chess.Move | None:
    color = board.turn
    rank = 0 if color == chess.WHITE else 7
    to_file = 2 if queenside else 6
    for move in board.legal_moves:
        if move.from_square == chess.square(4, rank) and move.to_square == chess.square(to_file, rank):
            return move
    return None


def _diagnose(board: chess.Board, from_sq: int | None, to_sq: int, piece: int | None) -> Resolution:
    name_to = chess.square_name(to_sq)

    if from_sq is not None:
        src = board.piece_at(from_sq)
        src_name = chess.square_name(from_sq)
        if src is None:
            return _err("no_piece", square=src_name)
        if src.color != board.turn:
            return _err("enemy_piece", square=src_name)
        if piece is not None and src.piece_type != piece:
            return _err("wrong_piece", square=src_name, actual=PIECE_RU[src.piece_type], wanted=PIECE_RU[piece])
        data = {"piece": PIECE_RU[src.piece_type], "target": name_to, "hints": _legal_targets(board, from_sq)[:8]}
        if board.is_pinned(board.turn, from_sq):
            data["pinned"] = True
        if src.piece_type == chess.KING and abs(chess.square_file(from_sq) - chess.square_file(to_sq)) == 2:
            data["castle_failed"] = True
        return _err("illegal", **data)

    target = board.piece_at(to_sq)
    if target is not None and target.color == board.turn:
        return _err("own_target", square=name_to)
    if piece is not None:
        return _err("illegal", piece=PIECE_RU[piece], target=name_to, hints=[], no_from=True)
    return _err("illegal", piece=None, target=name_to, hints=[], no_from=True)


def describe_move(board_before: chess.Board, move: chess.Move) -> str:
    """Человекочитаемое описание хода: 'e2 → e4', '0-0', 'e7 → e8 = ферзь'."""
    if board_before.is_castling(move):
        return "0-0-0" if board_before.is_queenside_castling(move) else "0-0"
    text = f"{chess.square_name(move.from_square)} → {chess.square_name(move.to_square)}"
    if move.promotion:
        text += f" = {PIECE_RU[move.promotion]}"
    return text
