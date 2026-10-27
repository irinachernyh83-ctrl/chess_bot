"""Оффлайн-проверки: парсер ходов, хранилище, тексты, рендер доски.

Запуск:  python tests/test_offline.py
"""

from __future__ import annotations

import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from chessbot import notation, texts
from chessbot import board as board_render
from chessbot.store import (
    MODE_INVITE,
    STATUS_ACTIVE,
    STATUS_FINISHED,
    STATUS_WAITING,
    Game,
    Store,
)

PASSED = 0


def ok(condition: bool, message: str) -> None:
    global PASSED
    if not condition:
        raise AssertionError(message)
    PASSED += 1


def expect_move(board: chess.Board, text: str, uci: str) -> None:
    result = notation.resolve(board, text)
    ok(result.ok, f"{text!r}: ожидался ход {uci}, получена ошибка {result.error} {result.data}")
    ok(result.move.uci() == uci, f"{text!r}: ожидался {uci}, получен {result.move.uci()}")


def expect_error(board: chess.Board, text: str, code: str) -> None:
    result = notation.resolve(board, text)
    ok(not result.ok, f"{text!r}: ожидалась ошибка {code}, получен ход {result.move}")
    ok(result.error == code, f"{text!r}: ожидалась ошибка {code}, получена {result.error}")


def test_start_position() -> None:
    b = chess.Board()
    expect_move(b, "пешка e4", "e2e4")
    expect_move(b, "пешка е4", "e2e4")          # кириллическая «е»
    expect_move(b, "e2 e4", "e2e4")
    expect_move(b, "e4", "e2e4")
    expect_move(b, "e2-e4", "e2e4")
    expect_move(b, "конь f3", "g1f3")
    expect_move(b, "конь g1 f3", "g1f3")
    expect_move(b, "конь c3", "b1c3")
    expect_move(b, "лошадь c3", "b1c3")

    expect_error(b, "слон c4", "illegal")
    expect_error(b, "ладья a1 a8", "illegal")
    expect_error(b, "пешка e5", "illegal")
    expect_error(b, "пешка e2 e5", "illegal")
    expect_error(b, "ферзь d1 h5", "illegal")
    expect_error(b, "король e1 e2", "illegal")
    expect_error(b, "пешка e7", "illegal")
    expect_error(b, "конь e5", "illegal")
    expect_error(b, "тура a1 h1", "illegal")
    expect_error(b, "0-0", "castle_impossible")
    expect_error(b, "рокировка", "castle_impossible")
    expect_error(b, "абвгд", "no_square")
    expect_error(b, "пешка", "no_square")
    expect_error(b, "e2 e4 f5 g6", "too_many_squares")


def test_piece_mismatch() -> None:
    b = chess.Board()
    expect_error(b, "конь e2 e4", "wrong_piece")
    result = notation.resolve(b, "конь e2 e4")
    ok(result.data.get("actual") == "пешка", "должна определиться реальная фигура на клетке")


def test_after_moves() -> None:
    b = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
        b.push_uci(uci)
    expect_move(b, "конь e5", "f3e5")
    expect_move(b, "конь f3 e5", "f3e5")
    expect_move(b, "пешка d4", "d2d4")
    expect_error(b, "пешка e4 e5", "illegal")

    # связка: белый конь f3 прикрывает ферзя? проверим диагностику
    b2 = chess.Board("4k3/8/8/8/8/8/4R3/4K3 w - - 0 1")
    expect_move(b2, "ладья e2 e8", "e2e8")


def test_castling() -> None:
    b = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    expect_move(b, "рокировка", "e1g1")
    expect_move(b, "0-0-0", "e1c1")
    expect_move(b, "длинная рокировка", "e1c1")
    expect_move(b, "короткая рокировка", "e1g1")
    expect_move(b, "король e1 g1", "e1g1")

    b2 = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1")
    expect_error(b2, "0-0-0", "castle_impossible")


def test_ambiguity() -> None:
    b = chess.Board("4k3/8/8/8/8/2N5/8/4K1N1 w - - 0 1")
    result = notation.resolve(b, "конь e2")
    ok(result.error == "ambiguous", f"ожидалась неоднозначность, получено {result}")
    ok(len(result.data.get("options", [])) == 2, "должно быть два варианта")
    expect_move(b, "конь c3 e2", "c3e2")
    expect_move(b, "конь g1 e2", "g1e2")


def test_promotion() -> None:
    b = chess.Board("k7/4P3/8/8/8/8/8/4K3 w - - 0 1")
    expect_move(b, "пешка e8", "e7e8q")
    expect_move(b, "e7 e8 ферзь", "e7e8q")
    expect_move(b, "пешка e7 e8 конь", "e7e8n")
    expect_move(b, "e7 e8=л", "e7e8r")
    q = chess.Board("k7/4P3/8/8/8/8/8/4K3 w - - 0 1")
    q.push_uci("e7e8q")
    ok(q.piece_at(chess.E8).piece_type == chess.QUEEN, "пешка должна стать ферзём")


def test_check_diagnostic() -> None:
    b = chess.Board("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1")
    result = notation.resolve(b, "король e1 f2")
    ok(result.error == "illegal", "ход короля на битое поле должен быть нелегальным")


def test_random_games() -> None:
    rng = random.Random(20261027)
    for _ in range(30):
        b = chess.Board()
        for _ply in range(120):
            if b.is_game_over():
                break
            before = b.copy()
            move = rng.choice(list(b.legal_moves))
            if before.is_castling(move):
                text = "0-0" if before.is_kingside_castling(move) else "0-0-0"
            else:
                piece = before.piece_type_at(move.from_square)
                text = (
                    f"{notation.PIECE_RU[piece]} "
                    f"{chess.square_name(move.from_square)} {chess.square_name(move.to_square)}"
                )
                if move.promotion:
                    text += f" {notation.PIECE_RU[move.promotion]}"
            result = notation.resolve(before, text)
            ok(result.ok, f"случайная партия: {text!r} не разобран ({result.error} {result.data})")
            ok(result.move == move, f"случайная партия: {text!r} -> {result.move}, ждали {move}")
            b.push(move)


def test_keyboards() -> None:
    """Клавиатуры должны сериализоваться — иначе Telegram отвергает отправку."""
    from telethon import TelegramClient

    from chessbot import keyboards

    game = Game(id=1, white_id=1, white_chat=1, black_id=2, black_chat=2,
                status=STATUS_ACTIVE, base_ms=300_000)
    game.start_clock(now=0.0)

    waiting = Game(id=2, white_id=1, white_chat=1, status=STATUS_WAITING, mode=MODE_INVITE,
                   invite_token="tok")
    finished = Game(id=3, white_id=1, white_chat=1, black_id=2, black_chat=2,
                    status=STATUS_FINISHED)

    cases = {
        "постоянное меню": keyboards.main_menu(),
        "выбор времени": keyboards.time_buttons(),
        "ожидание (личка)": keyboards.board_buttons(waiting, "chess_bot"),
        "активная партия": keyboards.board_buttons(game, "chess_bot"),
        "конец партии": keyboards.board_buttons(finished, "chess_bot"),
        "приглашение": keyboards.invite_offer_buttons("tok"),
        "подтверждение сдачи": keyboards.resign_buttons(1, 1),
        "ничья": keyboards.draw_buttons(1, 1),
        "инлайн-фигуры": keyboards.inline_board_buttons("tok", chess.Board()),
        "инлайн-клетки": keyboards.inline_board_buttons("tok", chess.Board(), "e2"),
        "инлайн-сдача": keyboards.inline_resign_confirm("tok"),
        "инлайн-ничья": keyboards.inline_draw_confirm("tok"),
    }
    for name, buttons in cases.items():
        markup = TelegramClient.build_reply_markup(buttons)
        ok(markup is not None, f"{name}: клавиатура собрана")
        markup._bytes()  # бросит исключение, если структура неверная
        ok(True, f"{name}: клавиатура сериализуется")

    menu = keyboards.main_menu()
    ok(all(type(row).__name__ == "KeyboardButtonRow" for row in menu.rows),
       "строки меню — KeyboardButtonRow")
    ok(menu.persistent and menu.resize, "меню закреплено и компактное")
    ok(keyboards.empty_markup() is None,
       "пустая разметка — None: пустой ReplyInlineMarkup Telegram отвергает")

    start = chess.Board()
    sources = keyboards.inline_board_buttons("tok", start)
    source_labels = [btn.text for row in sources[:-1] for btn in row]
    ok(len(source_labels) == 10, "на старте доступны 10 фигур: 8 пешек и 2 коня")
    ok("a2 пешка" in source_labels and "b2 пешка" in source_labels,
       "пешки подписаны клеткой и названием")
    ok("b1 конь" in source_labels and "g1 конь" in source_labels,
       "кони тоже могут ходить")
    ok("d1 ферзь" not in source_labels, "ферзь на старте скрыт")
    ok("c1 слон" not in source_labels, "слон на старте скрыт")
    ok("a1 ладья" not in source_labels, "ладья на старте скрыта")
    ok("e1 король" not in source_labels, "король на старте скрыт")
    ok(all(btn.data.startswith(b"iq:tok:") for row in sources[:-1] for btn in row),
       "кнопки фигур шлют клетку")
    ok(sources[-1][0].data == b"ir:tok" and sources[-1][1].data == b"id:tok",
       "кнопки сдачи и ничьей")

    targets = keyboards.inline_board_buttons("tok", start, "e2")
    target_labels = [btn.text for row in targets[:-2] for btn in row]
    ok(target_labels == ["e3", "e4"], "у пешки e2 только e3 и e4")
    ok(all(btn.data in (b"iq:tok:e3", b"iq:tok:e4")
           for row in targets[:-2] for btn in row),
       "клетки назначения шлют ход")
    ok(targets[-2][0].data == b"ic:tok" and "Назад" in targets[-2][0].text,
       "есть кнопка назад")
    ok(targets[-1][0].data == b"ir:tok", "управление осталось")

    knight = keyboards.inline_board_buttons("tok", start, "b1")
    knight_labels = [btn.text for row in knight[:-2] for btn in row]
    ok(set(knight_labels) == {"a3", "c3"}, "у коня b1 только a3 и c3")

    blocked = chess.Board("4k3/8/8/8/8/4p3/4P3/4K3 w - - 0 1")
    blocked_labels = [
        btn.text for row in keyboards.inline_board_buttons("tok", blocked)[:-1]
        for btn in row
    ]
    ok("e2 пешка" not in blocked_labels, "заблокированная пешка скрыта")
    ok("e1 король" in blocked_labels, "король с ходами показан")

    pinned = chess.Board("4r1k1/8/8/8/8/8/4B3/4K3 w - - 0 1")
    pinned_labels = [
        btn.text for row in keyboards.inline_board_buttons("tok", pinned)[:-1]
        for btn in row
    ]
    ok("e2 слон" not in pinned_labels, "связанный слон скрыт")
    ok("e1 король" in pinned_labels, "король показан")

    capture = chess.Board("4k3/8/8/8/8/3pp3/4P3/4K3 w - - 0 1")
    capture_targets = keyboards.inline_board_buttons("tok", capture, "e2")
    capture_labels = [btn.text for row in capture_targets[:-2] for btn in row]
    ok(capture_labels == ["d3 ×"], "взятие помечено крестиком")

    promo = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    promo_targets = keyboards.inline_board_buttons("tok", promo, "a7")
    promo_labels = [btn.text for row in promo_targets[:-2] for btn in row]
    ok(promo_labels == ["a8 ♛"], "превращение помечено ферзём")


def test_invites() -> None:
    from chessbot import invites

    secret = invites.make_secret("123456:AA-test")
    token = invites.make_token(secret, 42, 10, now=1000)
    ok(len(token) <= 60, "токен короткий")
    ok(len(f"ij:{token}".encode()) <= 64, "кнопка влезает в лимит callback data 64 байта")
    ok(invites.parse_token(secret, token, now=1000) == (42, 10), "токен разбирается")
    ok(invites.parse_token(secret, token, now=1000 + invites.TTL_SECONDS - 5) == (42, 10),
       "токен жив почти сутки")
    ok(invites.parse_token(secret, token, now=1000 + invites.TTL_SECONDS + 1) is None,
       "просроченный токен отклоняется")
    ok(invites.parse_token(b"wrong", token, now=1000) is None, "чужая подпись отклоняется")
    ok(invites.parse_token(secret, "garbage", now=1000) is None, "мусор отклоняется")
    ok(invites.parse_token(secret, "", now=1000) is None, "пустой токен отклоняется")

    forged = invites._b64(b"42:15:1000.0000000000")
    ok(invites.parse_token(secret, forged, now=1000) is None, "подделанный токен отклоняется")

    try:
        invites.make_token(secret, 42, 7)
        ok(False, "недопустимый контроль времени должен падать")
    except ValueError:
        ok(True, "недопустимый контроль времени отклоняется")

    another = invites.make_secret("999:BB-test")
    ok(invites.parse_token(another, token, now=1000) is None, "секрет привязан к токену бота")


def test_store() -> None:
    store = Store(":memory:")
    game = Game(
        mode=MODE_INVITE,
        white_id=10,
        white_name="Вася",
        white_chat=-100500,
        status=STATUS_WAITING,
        base_ms=5 * 60_000,
    )
    store.save(game)
    ok(game.id is not None, "партии присваивается id")

    loaded = store.get(game.id)
    ok(loaded is not None and loaded.white_chat == -100500, "партия должна сохраняться")
    ok(loaded.fen == chess.STARTING_FEN, "начальная позиция должна сохраняться")
    ok(store.current(-100500) is not None, "партия находится по чату")

    loaded.black_id = 20
    loaded.black_name = "Петя"
    loaded.black_chat = -200
    loaded.status = STATUS_ACTIVE
    loaded.invite_token = "tok-123"
    store.save(loaded)
    again = store.get(game.id)
    ok(again.black_id == 20 and again.status == STATUS_ACTIVE, "обновление партии")
    ok(store.current(-200) is not None, "партия находится и по чату соперника")
    ok(store.get_by_token("tok-123").id == game.id, "поиск по токену приглашения")

    # история ходов должна восстанавливаться
    again.moves = "e2e4 e7e5 g1f3"
    board = again.board()
    ok(board.piece_at(chess.E4) is not None, "доска восстановлена из истории")
    ok(board.piece_at(chess.F3) is not None, "ходы восстанавливаются по порядку")
    ok(len(board.move_stack) == 3, "история ходов должна сохраняться")
    again.fen = board.fen()
    store.save(again)
    restored = store.get(game.id)
    ok(restored.board().fen() == board.fen(), "позиция из истории совпадает с FEN")

    ok(not restored.is_invite is False, "режим приглашения сохраняется")

    store.delete(game.id)
    ok(store.get(game.id) is None, "удаление партии")
    ok(store.current(-100500) is None, "после удаления чат пуст")

    finished = Game(
        mode=MODE_INVITE,
        white_id=11,
        white_chat=-300,
        status=STATUS_FINISHED,
        result_text="готово",
    )
    store.save(finished)
    ok(store.finished_older_than(600) == [], "свежая оконченная партия не попадает в очистку")
    old = store.finished_older_than(600, now=time.time() + 601)
    ok([g.id for g in old] == [finished.id], "старая оконченная партия находится")
    store.close()


def test_clock() -> None:
    game = Game(white_id=1, white_name="Вася", white_chat=1, base_ms=5 * 60_000,
                status=STATUS_ACTIVE)
    game.black_id = 2
    game.black_name = "Петя"
    game.black_chat = 2
    game.start_clock(now=1000.0)
    ok(game.white_ms == 300_000 and game.black_ms == 300_000, "часы стартуют с полного времени")
    ok(game.remaining_ms(chess.WHITE, now=1000.0) == 300_000, "в момент старта время целое")
    ok(game.remaining_ms(chess.WHITE, now=1012.0) == 288_000, "часы белых идут")
    ok(game.remaining_ms(chess.BLACK, now=1012.0) == 300_000, "часы чёрных стоят")
    ok(game.flagged_color(now=1012.0) is None, "время ещё есть")

    game.spend_time(chess.WHITE, now=1012.0)
    ok(game.white_ms == 288_000, "время хода списалось у белых")
    ok(game.turn_started_at == 1012.0, "часы соперника запущены")
    ok(game.remaining_ms(chess.BLACK, now=1012.0) == 300_000, "у чёрных полный запас")

    game.status = STATUS_FINISHED
    ok(game.remaining_ms(chess.WHITE, now=9999.0) == 288_000, "после конца партии часы стоят")

    overtime = Game(white_id=1, white_chat=1, black_id=2, black_chat=2,
                    base_ms=60_000, status=STATUS_ACTIVE)
    overtime.start_clock(now=500.0)
    ok(overtime.flagged_color(now=561.0) == chess.WHITE, "просрочивший определяется")
    ok(overtime.remaining_ms(chess.WHITE, now=561.0) == 0, "время не уходит в минус")

    untimed = Game(white_id=1, white_chat=1, black_id=2, black_chat=2, status=STATUS_ACTIVE)
    ok(untimed.flagged_color(now=10_000.0) is None, "без контроля времени никто не проигрывает")
    ok(untimed.remaining_ms(chess.WHITE) is None, "без часов остаток не считается")


def test_texts() -> None:
    game = Game(white_id=1, white_name="Вася", white_chat=1, status=STATUS_WAITING)
    waiting = texts.board_caption(game, chess.Board(), "chess_bot")
    ok(len(waiting) < 1024, "подпись для ожидания должна влезать в caption")
    ok("@sav3my" in texts.help_text("chess_bot"), "в помощи есть контакт @sav3my")
    ok("@sav3my" in texts.start_text("chess_bot"), "контакт есть и в приветствии")

    game.black_id = 2
    game.black_name = "Петя"
    game.black_chat = 2
    game.status = STATUS_ACTIVE
    game.base_ms = 5 * 60_000
    game.start_clock(now=0.0)
    board = chess.Board()
    active = texts.board_caption(game, board, "chess_bot")
    ok("пешка e4" in active, "в подписи должна быть подсказка по ходу")
    ok("5:00" in active, "в подписи должны быть часы")
    ok(len(active) < 1024, "подпись активной партии должна влезать в caption")

    board.push_uci("f2f3")
    board.push_uci("e7e5")
    board.push_uci("g2g4")
    board.push_uci("d8h4")
    ok(board.is_checkmate(), "должен быть мат (детский мат)")
    game.status = "finished"
    game.result_text = texts.result_checkmate(board, game)
    finished = texts.board_caption(game, board, "chess_bot")
    ok("Мат" in finished, "подпись должна сообщать о мате")

    text_board = texts.inline_text_board(chess.Board())
    ok(text_board.startswith("<pre>") and text_board.endswith("</pre>"),
       "текстовая доска — в блоке pre")
    ok("r n b q k b n r" in text_board, "чёрные стоят на 8-й горизонтали")
    ok("P P P P P P P P" in text_board, "белые пешки на 2-й горизонтали")
    ok("a b c d e f g h" in text_board, "под доской есть буквы файлов")

    for code, data in (
        ("no_square", {}),
        ("no_piece", {"square": "e4"}),
        ("enemy_piece", {"square": "e7"}),
        ("wrong_piece", {"square": "e2", "actual": "пешка", "wanted": "конь"}),
        ("own_target", {"square": "d2"}),
        ("ambiguous", {"options": ["конь b1 f3", "конь g1 f3"]}),
        ("castle_impossible", {"reason": "rights"}),
        ("illegal", {"piece": "ладья", "target": "a8", "hints": ["a2", "a3"]}),
        ("illegal", {"piece": "слон", "target": "h5", "hints": [], "pinned": True}),
    ):
        message = texts.move_error_text(code, data)
        ok(isinstance(message, str) and len(message) > 0, f"текст ошибки для {code}")


def test_render() -> None:
    png = board_render.render_cached(chess.STARTING_FEN, None)
    ok(png.startswith(b"\x89PNG"), "должен получиться PNG")
    ok(len(png) > 5000, "картинка не должна быть пустой")
    b = chess.Board()
    b.push_uci("e2e4")
    png2 = board_render.render_cached(b.fen(), "e2e4")
    ok(png2 != png, "позиции должны отличаться")

    os.makedirs("tests/out", exist_ok=True)
    with open("tests/out/start.png", "wb") as f:
        f.write(png)


def test_health_port() -> None:
    from chessbot import app as app_module

    keys = ("PORT", "HEALTH_PORT")
    saved = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        ok(app_module._health_port() is None, "без PORT health-сервер не нужен")
        os.environ["PORT"] = "8080"
        ok(app_module._health_port() == 8080, "PORT должен читаться")
        os.environ["HEALTH_PORT"] = "9090"
        ok(app_module._health_port() == 9090, "HEALTH_PORT важнее PORT")
        os.environ["HEALTH_PORT"] = "0"
        try:
            app_module._health_port()
        except SystemExit:
            pass
        else:
            raise AssertionError("нулевой порт должен отвергаться")
        os.environ["HEALTH_PORT"] = "не-число"
        try:
            app_module._health_port()
        except SystemExit:
            pass
        else:
            raise AssertionError("нечисловой порт должен отвергаться")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    tests = [
        test_start_position,
        test_piece_mismatch,
        test_after_moves,
        test_castling,
        test_ambiguity,
        test_promotion,
        test_check_diagnostic,
        test_random_games,
        test_store,
        test_clock,
        test_texts,
        test_keyboards,
        test_invites,
        test_render,
        test_health_port,
    ]
    for test in tests:
        test()
        print(f"  ✓ {test.__name__}")
    print(f"\nВсе проверки пройдены: {PASSED} утверждений")


if __name__ == "__main__":
    main()
