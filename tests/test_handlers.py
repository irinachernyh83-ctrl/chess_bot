"""Интеграционные проверки обработчиков без реального Telegram.

Подменяем Telethon-клиент и события заглушками, но проходим весь путь:
создание партии -> приглашение в личке -> принятие -> ходы -> часы ->
сдача/ничья/таймаут. Отдельно проверяется режим группы.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from telethon.tl import functions

from chessbot import board as board_render
from chessbot import handlers
from chessbot.handlers import ChessBot
from chessbot.store import STATUS_ACTIVE, STATUS_FINISHED, STATUS_WAITING, Store

# не рисуем настоящие PNG в тестах — это медленно
handlers.board_render.render_cached = lambda *a, **k: b"\x89PNG\r\n"
board_render.render_cached = handlers.board_render.render_cached

PASSED = 0


def ok(condition: bool, message: str) -> None:
    global PASSED
    if not condition:
        raise AssertionError(message)
    PASSED += 1


class FakeClient:
    def __init__(self) -> None:
        self.me = SimpleNamespace(
            id=999, username="chess_test_bot", first_name="Chess", last_name="Bot", bot=True
        )
        self.messages: list[dict] = []
        self.files: list[dict] = []
        self.edits: list[dict] = []
        self.requests: list = []
        self.uploads: list = []
        self.last_media = None
        self.deletions: list[tuple[int, int]] = []
        self._mid = 100
        # если True — редактирование картинкой падает (проверяем текстовый запасной вариант)
        self.fail_photo_edits = False

    async def get_me(self):
        return self.me

    async def get_entity(self, entity):
        if isinstance(entity, int):
            names = {1: "Вася", 2: "Петя", 3: "Коля"}
            return user(entity, names.get(entity, "Игрок"))
        return entity

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.messages.UploadMediaRequest):
            self.last_media = SimpleNamespace(
                photo=SimpleNamespace(id=1, access_hash=2, file_reference=b""),
                spoiler=None,
                ttl_seconds=None,
            )
            return self.last_media
        return None

    async def upload_file(self, file, **kwargs):
        self.uploads.append(file)
        return SimpleNamespace(id=len(self.uploads), parts=1, name="board.png")

    def _next(self) -> int:
        self._mid += 1
        return self._mid

    async def send_message(self, chat_id, text=None, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(id=self._next())

    async def send_file(self, chat_id, file=None, caption=None, **kwargs):
        self.files.append({"chat_id": chat_id, "caption": caption, "file": file, **kwargs})
        return SimpleNamespace(id=self._next())

    async def edit_message(self, chat_id, message_id=None, text=None, file=None, **kwargs):
        if file is not None and self.fail_photo_edits:
            raise RuntimeError("photo edit is not allowed")
        self.edits.append(
            {"chat_id": chat_id, "id": message_id, "text": text, "file": file, **kwargs}
        )
        return SimpleNamespace(id=message_id or 0)

    async def delete_messages(self, chat_id, message_ids, **kwargs):
        ids = message_ids if isinstance(message_ids, (list, tuple)) else [message_ids]
        for message_id in ids:
            self.deletions.append((chat_id, message_id))
        return len(ids)


class FakeMessageEvent:
    def __init__(self, chat_id, sender, text="", message_id=1, is_private=False):
        self.chat_id = chat_id
        self.sender_id = sender.id
        self.raw_text = text
        self.message = SimpleNamespace(id=message_id, reply_to_msg_id=None)
        self.is_private = is_private
        self.is_group = not is_private
        self.is_channel = False
        self.is_reply = False
        self.out = False
        self.sender = sender

    async def get_sender(self):
        return self.sender

    async def get_chat(self):
        return SimpleNamespace(id=self.chat_id)

    async def get_reply_message(self):
        return None


class FakeInlineMessageId:
    """Минимальная заглушка InputBotInlineMessageID для тестов."""

    def __init__(self, number: int = 1) -> None:
        self.number = number
        self.dc_id = 2
        self.id = 1000 + number
        self.access_hash = 123456

    def _bytes(self) -> bytes:
        return f"inline-message-{self.number}".encode()


class FakeCallbackEvent:
    def __init__(
        self,
        chat_id,
        sender,
        data,
        message_id=10,
        is_private=False,
        *,
        via_inline=False,
        inline_msg_id=None,
    ):
        self.chat_id = chat_id
        self.sender_id = sender.id
        self.data = data.encode() if isinstance(data, str) else data
        self.message_id = message_id
        self.message = SimpleNamespace(id=message_id)
        self.is_private = is_private
        self.is_group = not is_private
        self.sender = sender
        self.via_inline = via_inline
        self.query = SimpleNamespace(
            msg_id=inline_msg_id,
            data=self.data,
            chat_instance="test-chat-instance",
            query_id=message_id,
        )
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, alert=False):
        self.answers.append((text, alert))

    async def get_sender(self):
        return self.sender


class FakeInlineEvent:
    def __init__(self, sender, text=""):
        self.sender = sender
        self.sender_id = sender.id
        self.text = text
        self.results: list | None = None
        self.answers: list[dict] = []
        self.builder = self

    async def article(self, **kwargs):
        return kwargs

    async def get_sender(self):
        return self.sender

    async def answer(self, results=None, **kwargs):
        self.results = results
        self.answers.append(kwargs)


def user(user_id, name):
    return SimpleNamespace(id=user_id, first_name=name, last_name="", username=f"u{user_id}", bot=False)


async def make_bot(store):
    client = FakeClient()
    bot = ChessBot(client, store)
    await bot.setup()
    return bot, client


def msg(chat_id, sender, text, is_private=False, message_id=1):
    return FakeMessageEvent(chat_id, sender, text, message_id=message_id, is_private=is_private)


def cb(chat_id, sender, data, is_private=False):
    return FakeCallbackEvent(chat_id, sender, data, is_private=is_private)


def texts_in(client, chat_id) -> list[str]:
    return [m["text"] or "" for m in client.messages if m["chat_id"] == chat_id]


def last_text(client, chat_id=None) -> str:
    items = [m for m in client.messages if chat_id is None or m["chat_id"] == chat_id]
    return items[-1]["text"] or "" if items else ""


def any_text(client, needle: str, chat_id=None) -> bool:
    return any(needle in text for text in texts_in(client, chat_id)) if chat_id else any(
        needle in (m["text"] or "") for m in client.messages
    )


def any_edit(client, needle: str, chat_id=None) -> bool:
    return any(
        needle in (e["text"] or "")
        for e in client.edits
        if chat_id is None or e["chat_id"] == chat_id
    )


def boards_for(client, chat_id) -> list[str]:
    captions = [f["caption"] or "" for f in client.files if f["chat_id"] == chat_id]
    captions += [e["text"] or "" for e in client.edits if e["chat_id"] == chat_id and e["file"]]
    return captions


def last_buttons(client) -> list | None:
    for item in reversed(client.messages):
        if item.get("buttons"):
            return item["buttons"]
    return None


def _rows(buttons):
    if buttons is None:
        return []
    return getattr(buttons, "rows", buttons)


def any_button(client, label: str, chat_id=None) -> bool:
    for item in client.messages:
        if chat_id is not None and item["chat_id"] != chat_id:
            continue
        for row in _rows(item.get("buttons")):
            for button in getattr(row, "buttons", row):
                if label in getattr(button, "text", ""):
                    return True
    return False


async def create_invite(bot, client, white, chat_id=1001, minutes=5):
    await bot.on_message(msg(chat_id, white, "/new", is_private=True))
    await bot.on_callback(cb(chat_id, white, f"tc:{minutes}", is_private=True))
    return bot.store.current(chat_id)


async def accept_invite(bot, game, black, chat_id=1002):
    token = bot.store.get(game.id).invite_token
    await bot.on_message(msg(chat_id, black, f"/start g_{token}", is_private=True))
    await bot.on_callback(cb(chat_id, black, f"jac:{token}", is_private=True))


async def test_private_invite_full_game() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")

    # --- /start даёт постоянное меню-клавиатуру
    await bot.on_message(msg(1001, white, "/start", is_private=True))
    ok(any_button(client, "Новая партия", 1001), "в приветствии есть кнопка старта")
    menu = last_buttons(client)
    ok(type(menu).__name__ == "ReplyKeyboardMarkup", "меню — клавиатура под полем ввода")
    ok(any_button(client, "Сдаться", 1001) and any_button(client, "Помощь", 1001),
       "в меню есть кнопки сдачи и помощи")

    # кнопка меню «♟ Новая партия» работает как команда
    await bot.on_message(msg(1001, white, "♟ Новая партия", is_private=True))
    ok("контроль времени" in last_text(client, 1001).lower(), "кнопка меню создаёт партию")

    # --- /new спрашивает контроль времени
    await bot.on_message(msg(1001, white, "/new", is_private=True))
    ok("контроль времени" in last_text(client, 1001).lower(), "бот предлагает выбрать время")
    buttons = last_buttons(client)
    ok(buttons is not None, "кнопки выбора времени отправлены")
    ok(len(buttons) >= 2, "три варианта времени и отмена")

    # --- выбор 5 минут создаёт партию с приглашением
    await bot.on_callback(cb(1001, white, "tc:5", is_private=True))
    game = store.current(1001)
    ok(game is not None and game.status == STATUS_WAITING, "партия ждёт соперника")
    ok(game.is_invite, "режим личного приглашения")
    ok(game.base_ms == 5 * 60_000, "контроль времени 5 минут")
    ok(bool(game.invite_token), "есть токен приглашения")
    invite = f"t.me/chess_test_bot?start=g_{game.invite_token}"
    ok(any(f"https://{invite}" in (f["caption"] or "") for f in client.files),
       "в подписи доски есть ссылка-приглашение")
    ok(game.black_id is None, "соперника ещё нет")

    # --- открытие ссылки показывает предложение принять/отказаться
    await bot.on_message(
        msg(1002, black, f"/start g_{game.invite_token}", is_private=True)
    )
    offer = last_text(client, 1002)
    ok("Приглашение в шахматы" in offer, "приглашённому показано приглашение")
    ok("5 мин" in offer, "в приглашении указан контроль времени")
    ok(any_text(client, "Принять", 1002), "кнопки принятия отправлены")

    # --- принятие запускает партию и часы
    await bot.on_callback(cb(1002, black, f"jac:{game.invite_token}", is_private=True))
    game = store.get(game.id)
    ok(game.status == STATUS_ACTIVE, "после принятия партия активна")
    ok(game.black_id == 2 and game.black_chat == 1002, "соперник привязан к своему чату")
    ok(game.white_ms == 300_000 and game.black_ms == 300_000, "часы выставлены обоим")
    ok(game.turn_started_at is not None, "часы белых запущены")
    ok(any("ход белых" in c for c in boards_for(client, 1002)), "чёрным отправлена доска")
    ok(any_text(client, "принял", 1001), "белым сообщили о принятии")

    # --- ход белых без упоминания бота работает в личке
    await bot.on_message(msg(1001, white, "пешка e4", is_private=True))
    game = store.get(game.id)
    ok(game.board().piece_at(chess.E4) is not None, "пешка стоит на e4")
    ok(game.board().turn == chess.BLACK, "ход перешёл чёрным")
    ok(game.last_move == "e2 → e4", f"последний ход: {game.last_move!r}")
    ok(game.white_ms <= 300_000, "время белых списалось")
    ok(any("e2 → e4" in c for c in boards_for(client, 1001)), "ход попал в подпись доски")
    ok(any_text(client, "Твой ход", 1002), "сопернику пришло уведомление о ходе")

    # --- белые не могут сходить дважды: ждут соперника
    before = store.get(game.id).fen
    await bot.on_message(msg(1001, white, "пешка d4", is_private=True))
    ok("Сейчас ход чёрных" in last_text(client, 1001), "белые ждут хода соперника")
    ok(store.get(game.id).fen == before, "позиция не изменилась")

    # --- чёрные ходят в своём чате
    await bot.on_message(msg(1002, black, "пешка e5", is_private=True))
    game = store.get(game.id)
    ok(game.board().piece_at(chess.E5) is not None, "ответ чёрных применён")
    ok(game.board().turn == chess.WHITE, "ход снова у белых")

    # --- чёрные не могут сходить дважды
    before = store.get(game.id).fen
    await bot.on_message(msg(1002, black, "конь c6", is_private=True))
    ok("Сейчас ход белых" in last_text(client, 1002), "чёрные ждут хода соперника")
    ok(store.get(game.id).fen == before, "позиция не изменилась и тут")

    # --- нелегальный ход объясняется
    await bot.on_message(msg(1001, white, "пешка e5", is_private=True))
    ok("не может" in last_text(client, 1001), "нелегальный ход объяснён")
    ok(store.get(game.id).board().piece_at(chess.E5).color == chess.BLACK, "фигура не подвинулась")

    # --- ничья: предложение уходит сопернику в его чат
    await bot.on_message(msg(1001, white, "/draw", is_private=True))
    ok(any_text(client, "предлагает ничью", 1002), "чёрным пришло предложение ничьей")
    own = cb(1001, white, f"draw_yes:{game.id}:1", is_private=True)
    await bot.on_callback(own)
    ok("соперник" in (own.answers[-1][0] or ""), "нельзя принять свою ничью")
    ok(store.get(game.id).status == STATUS_ACTIVE, "партия продолжается")

    accept = cb(1002, black, f"draw_yes:{game.id}:1", is_private=True)
    await bot.on_callback(accept)
    game = store.get(game.id)
    ok(game.status == STATUS_FINISHED, "ничья завершает партию")
    ok("Ничья" in (game.result_text or ""), "в результате ничья")
    ok(any_text(client, "ничью", 1001), "белым сообщили о ничьей")

    # --- после конца партии ходы не принимаются
    await bot.on_message(msg(1001, white, "пешка d4", is_private=True))
    ok("уже окончена" in last_text(client, 1001), "партия окончена")

    store.close()


async def test_invite_declined_and_busy() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")
    other = user(3, "Коля")

    game = await create_invite(bot, client, white)
    token = game.invite_token

    # отказ
    await bot.on_message(msg(1002, black, f"/start g_{token}", is_private=True))
    await bot.on_callback(cb(1002, black, f"jdc:{token}", is_private=True))
    ok(any_text(client, "отклонил", 1001), "белым сообщили об отказе")
    ok(store.get(game.id).status == STATUS_WAITING, "после отказа приглашение ещё активно")

    # своё приглашение нельзя принять
    own = cb(1001, white, f"jac:{token}", is_private=True)
    await bot.on_callback(own)
    ok(store.get(game.id).status == STATUS_WAITING, "своё приглашение не принимается")

    # принимаем другим игроком
    await bot.on_message(msg(1003, other, f"/start g_{token}", is_private=True))
    await bot.on_callback(cb(1003, other, f"jac:{token}", is_private=True))
    ok(store.get(game.id).status == STATUS_ACTIVE, "партия началась с третьим игроком")

    # второе приглашение тому же игроку не создать (занят)
    bot2_store = store
    game2 = await create_invite(bot, client, white, chat_id=1004)
    token2 = game2.invite_token
    await bot.on_message(msg(1003, other, f"/start g_{token2}", is_private=True))
    busy = cb(1003, other, f"jac:{token2}", is_private=True)
    await bot.on_callback(busy)
    ok("текущую партию" in (busy.answers[-1][0] or ""), "занятому игроку отказано")
    ok(bot2_store.get(game2.id).status == STATUS_WAITING, "вторая партия не началась")

    store.close()


async def test_group_flow_and_resign() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")
    group = -500

    await bot.on_message(msg(group, white, "/new"))
    ok("контроль времени" in last_text(client, group).lower(), "в группе тоже выбор времени")

    await bot.on_callback(cb(group, white, "tc:10"))
    game = store.current(group)
    ok(game.status == STATUS_WAITING and game.mode == "group", "партия в группе ждёт соперника")
    ok(game.base_ms == 600_000, "контроль 10 минут")
    ok(game.black_chat == group, "оба игрока в одном чате")

    join = cb(group, black, f"join:{game.id}")
    await bot.on_callback(join)
    game = store.get(game.id)
    ok(game.status == STATUS_ACTIVE and game.black_id == 2, "присоединение по кнопке")
    ok(game.white_ms == 600_000 and game.black_ms == 600_000, "часы в групповой партии")

    await bot.on_message(msg(group, white, "@chess_test_bot пешка e4"))
    ok(store.get(game.id).board().piece_at(chess.E4) is not None, "ход белых в группе")

    # ровно та проверка, что просили: один сходил — ждёт другого
    await bot.on_message(msg(group, white, "@chess_test_bot пешка d4"))
    ok("Сейчас ход чёрных" in last_text(client, group), "белые ждут ответа соперника")

    await bot.on_message(msg(group, black, "@chess_test_bot пешка e5"))
    ok(store.get(game.id).board().piece_at(chess.E5) is not None, "ответ чёрных")
    await bot.on_message(msg(group, black, "@chess_test_bot конь c6"))
    ok("Сейчас ход белых" in last_text(client, group), "чёрные ждут ответа соперника")

    # сдача с подтверждением
    await bot.on_message(msg(group, white, "@chess_test_bot сдаюсь"))
    ok(any_text(client, "точно сдаёшься", group), "запрос подтверждения сдачи")
    ok(store.get(game.id).status == STATUS_ACTIVE, "до подтверждения партия идёт")

    wrong = cb(group, black, f"resign_ok:{game.id}:1")
    await bot.on_callback(wrong)
    ok("не для тебя" in (wrong.answers[-1][0] or ""), "чужое подтверждение отклонено")
    ok(store.get(game.id).status == STATUS_ACTIVE, "чужое подтверждение не завершает партию")

    right = cb(group, white, f"resign_ok:{game.id}:1")
    await bot.on_callback(right)
    game = store.get(game.id)
    ok(game.status == STATUS_FINISHED, "подтверждённая сдача завершает партию")
    ok("сдался" in (game.result_text or ""), "в результате сдача")
    ok("Победа чёрных" in (game.result_text or ""), "победа достаётся сопернику")

    store.close()


async def test_timeout() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")

    game = await create_invite(bot, client, white, minutes=5)
    await accept_invite(bot, game, black)

    # белые «думали» 6 минут
    game = store.get(game.id)
    game.turn_started_at = time.time() - 6 * 60
    store.save(game)

    await bot.check_clocks()
    game = store.get(game.id)
    ok(game.status == STATUS_FINISHED, "просрочка времени завершает партию")
    ok("Время вышло" in (game.result_text or ""), "в результате — время вышло")
    ok("Победа чёрных" in (game.result_text or ""), "победа у соперника")
    ok(game.white_ms == 0, "у просрочившего ноль на часах")
    ok(any_text(client, "Время вышло", 1002), "соперник получил уведомление")
    ok(any("Время вышло" in c for c in boards_for(client, 1001)), "доска белых обновилась")

    # ход после просрочки уже не принимается
    await bot.on_message(msg(1001, white, "пешка d4", is_private=True))
    ok("уже окончена" in last_text(client, 1001), "после таймаута ходы не принимаются")

    store.close()


async def test_cancel_waiting_and_group_abort() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")

    game = await create_invite(bot, client, white)
    await bot.on_callback(cb(1001, white, f"cancel:{game.id}", is_private=True))
    game = store.get(game.id)
    ok(game.status == STATUS_FINISHED, "ожидающую партию можно отменить")
    ok("отменена" in (game.result_text or ""), "в результате отмена")

    group = -700
    await bot.on_message(msg(group, white, "/new"))
    await bot.on_callback(cb(group, white, "tc:15"))
    game = store.current(group)
    ok(game.base_ms == 900_000, "контроль 15 минут")
    await bot.on_callback(cb(group, black, f"join:{game.id}"))
    await bot.on_message(msg(group, black, "/cancel"))
    game = store.get(game.id)
    ok(game.status == STATUS_FINISHED, "участник может отменить партию")
    ok("отменена" in (game.result_text or ""), "в результате отмена")

    # посторонний не отменяет
    await bot.on_message(msg(group, white, "/new"))
    await bot.on_callback(cb(group, white, "tc:5"))
    game = store.current(group)
    stranger = user(3, "Коля")
    await bot.on_message(msg(group, stranger, "/cancel"))
    ok("не участвуешь" in last_text(client, group), "посторонний не может отменить")
    ok(store.get(game.id).status == STATUS_WAITING, "партия продолжает ждать")

    store.close()


async def test_time_control_rejected_and_new_button() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")
    group = -800

    await bot.on_message(msg(group, white, "/new"))
    # второй участник не может выбрать время за создателя
    wrong = cb(group, black, "tc:5")
    await bot.on_callback(wrong)
    ok("кто создал партию" in (wrong.answers[-1][0] or ""), "время выбирает создатель")
    ok(store.current(group) is None, "партия не создана чужой кнопкой")

    await bot.on_callback(cb(group, white, "tc:5"))
    game = store.current(group)
    await bot.on_callback(cb(group, black, f"join:{game.id}"))
    await bot.on_message(msg(group, black, "/cancel"))
    ok(store.get(game.id).status == STATUS_FINISHED, "партия завершена")

    # кнопка «Новая партия» снова спрашивает время
    await bot.on_callback(cb(group, white, "new"))
    ok("контроль времени" in last_text(client, group).lower(), "кнопка новой партии работает")
    await bot.on_callback(cb(group, white, "tc:15"))
    game = store.current(group)
    ok(game.status == STATUS_WAITING and game.base_ms == 900_000, "новая партия на 15 минут")

    store.close()


async def test_menu_buttons() -> None:
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")

    game = await create_invite(bot, client, white)
    await accept_invite(bot, game, black)
    game = store.get(game.id)

    # «📋 Доска» присылает доску ещё раз
    before = len([f for f in client.files if f["chat_id"] == 1001])
    await bot.on_message(msg(1001, white, "📋 Доска", is_private=True))
    after = len([f for f in client.files if f["chat_id"] == 1001])
    ok(after == before + 1, "кнопка «Доска» присылает новую доску")

    # «🤝 Ничья» отправляет предложение сопернику
    await bot.on_message(msg(1001, white, "🤝 Ничья", is_private=True))
    ok(any_text(client, "предлагает ничью", 1002), "кнопка ничьей работает")

    # «🏳️ Сдаться» спрашивает подтверждение
    await bot.on_message(msg(1001, white, "🏳️ Сдаться", is_private=True))
    ok(any_text(client, "точно сдаёшься", 1001), "кнопка сдачи спрашивает подтверждение")
    await bot.on_callback(cb(1001, white, f"resign_no:{game.id}:1", is_private=True))
    ok(store.get(game.id).status == STATUS_ACTIVE, "после отказа от сдачи игра идёт")

    # «❌ Отменить» завершает партию
    await bot.on_message(msg(1001, white, "❌ Отменить", is_private=True))
    ok(store.get(game.id).status == STATUS_FINISHED, "кнопка отмены работает")

    # «❓ Помощь» присылает справку с меню
    await bot.on_message(msg(1001, white, "❓ Помощь", is_private=True))
    ok("Примеры ходов" in last_text(client, 1001), "кнопка помощи присылает справку")

    store.close()


async def test_notify_falls_back_without_keyboard() -> None:
    """Если клавиатуру не удалось отправить, сообщение всё равно должно дойти."""

    class StrictClient(FakeClient):
        async def send_message(self, chat_id, text=None, **kwargs):
            if kwargs.get("buttons") is not None and type(kwargs["buttons"]).__name__ == "ReplyKeyboardMarkup":
                raise ValueError("a TLObject was expected but found something else")
            return await super().send_message(chat_id, text, **kwargs)

    store = Store(":memory:")
    client = StrictClient()
    bot = ChessBot(client, store)
    await bot.setup()

    await bot.cmd_start(msg(1001, user(1, "Вася"), "/start", is_private=True))
    ok(any_text(client, "Шахматы прямо в чате", 1001), "приветствие дошло без клавиатуры")
    ok(not any_button(client, "Новая партия", 1001), "клавиатура не прикрепилась")

    store.close()


async def test_inline_invite_flow() -> None:
    from chessbot import invites

    secret = invites.make_secret("123:AA-test")
    store = Store(":memory:")
    client = FakeClient()
    bot = ChessBot(client, store, inline_secret=secret)
    await bot.setup()

    white = user(1, "Вася")
    black = user(2, "Петя")
    stranger = user(3, "Коля")

    # --- инлайн-запрос даёт три варианта времени
    inline = FakeInlineEvent(white)
    await bot.on_inline(inline)
    ok(inline.results is not None and len(inline.results) == 3, "три результата инлайна")
    titles = [r["title"] for r in inline.results]
    ok(any("5 мин" in t for t in titles), "есть вариант 5 минут")
    ok(any("10 мин" in t for t in titles), "есть вариант 10 минут")
    ok(any("15 мин" in t for t in titles), "есть вариант 15 минут")
    ok(all("приглашает" in r["text"] for r in inline.results), "в тексте есть приглашение")
    ok(inline.answers and inline.answers[-1].get("private") is True, "ответ персональный")

    ten = [r for r in inline.results if "10 мин" in r["title"]][0]
    data = ten["buttons"][0].data.decode()
    ok(data.startswith("ij:"), "кнопка содержит подписанный токен")
    token = data[3:]
    ok(invites.parse_token(secret, token) == (1, 10), "токен указывает на автора и время")

    # --- чужое приглашение в группе начинает партию
    join = cb(-600, black, data)
    await bot.on_callback(join)
    game = store.current(-600)
    ok(game is not None and game.status == STATUS_ACTIVE, "инлайн-партия началась")
    ok(game.white_id == 1 and game.black_id == 2, "белый — автор приглашения")
    ok(game.white_chat == -600 and game.black_chat == -600, "оба играют в этом чате")
    ok(game.base_ms == 600_000, "выбранный контроль времени применён")
    ok(game.white_ms == 600_000, "часы выставлены")
    ok(any("ход белых" in c for c in boards_for(client, -600)), "доска отправлена в чат")
    ok(any_text(client, "Партия началась", -600), "анонс начала партии")
    ok("чёрными" in (join.answers[-1][0] or ""), "игроку подтвердили цвет")

    # --- автор не может играть сам с собой по своей кнопке
    own = cb(-601, white, data)
    await bot.on_callback(own)
    ok("твоё приглашение" in (own.answers[-1][0] or ""), "своё приглашение отклонено")

    # --- в чате уже идёт партия
    second = cb(-600, stranger, data)
    await bot.on_callback(second)
    ok("уже есть партия" in (second.answers[-1][0] or ""), "вторая партия в чате не начинается")

    # --- битый токен
    broken = cb(-602, black, "ij:garbage")
    await bot.on_callback(broken)
    ok("устарело" in (broken.answers[-1][0] or ""), "битый токен отклонён")

    # --- после начала партии ходы работают как обычно
    await bot.on_message(msg(-600, white, "@chess_test_bot пешка e4"))
    ok(store.get(game.id).board().piece_at(chess.E4) is not None, "ход белых в инлайн-партии")
    await bot.on_message(msg(-600, white, "@chess_test_bot пешка d4"))
    ok("Сейчас ход чёрных" in last_text(client, -600), "белые ждут соперника")

    await bot.on_message(msg(-600, black, "@chess_test_bot пешка e5"))
    game = store.get(game.id)
    ok(game.board().piece_at(chess.E5) is not None, "ответ чёрных")
    ok(game.white_ms <= 600_000 and game.black_ms <= 600_000, "часы идут у обоих по очереди")

    store.close()


async def test_inline_board_flow() -> None:
    """Игра прямо в инлайн-сообщении: без чата с ботом и без Start."""
    from chessbot import invites

    secret = invites.make_secret("123:AA-test")
    store = Store(":memory:")
    client = FakeClient()
    bot = ChessBot(client, store, inline_secret=secret)
    await bot.setup()

    white = user(1, "Вася")
    black = user(2, "Петя")
    stranger = user(3, "Коля")

    def inline_cb(sender, data, msg_id):
        return FakeCallbackEvent(
            1,
            sender,
            data,
            is_private=True,
            via_inline=True,
            inline_msg_id=msg_id,
        )

    msg = FakeInlineMessageId(1)
    token = invites.make_token(secret, 1, 5)
    join = inline_cb(black, f"ij:{token}", msg)
    await bot.on_callback(join)
    inline = next(iter(bot._inline_games.values()))
    ok(inline.game.status == STATUS_ACTIVE, "инлайн-доска началась")
    ok(inline.game.white_id == 1 and inline.game.black_id == 2,
       "белый — автор, чёрный — нажавший")
    ok(inline.game.base_ms == 300_000, "в инлайн-партии 5 минут")
    ok(client.edits and client.edits[-1]["chat_id"] is msg,
       "отредактировано именно инлайн-сообщение")
    ok(client.edits[-1]["file"] is not None, "доска отправлена картинкой")
    ok(len(client.uploads) == 1, "фото заранее загружено на сервер")
    ok(client.edits[-1]["file"] is client.last_media,
       "в редактирование ушло готовое фото, а не сырой файл")
    ok("Шахматы" in (client.edits[-1]["text"] or ""), "подпись доски")
    join_buttons = client.edits[-1]["buttons"]
    join_labels = [btn.text for row in join_buttons[:-1] for btn in row]
    ok("a2 пешка" in join_labels and "b2 пешка" in join_labels,
       "белым показаны только фигуры, которые могут ходить")
    ok("d1 ферзь" not in join_labels, "ферзь на старте скрыт")
    ok("c1 слон" not in join_labels, "слон на старте скрыт")
    ok("b1 конь" in join_labels and "g1 конь" in join_labels, "кони на старте показаны")
    ok(len(join_labels) == 10, "на старте 10 доступных фигур")
    ok("чёрными" in (join.answers[-1][0] or ""), "нажавшему подтвердили цвет")

    # --- второй раз по тому же приглашению партию не начать
    again = inline_cb(stranger, f"ij:{token}", msg)
    await bot.on_callback(again)
    ok("уже началась" in (again.answers[-1][0] or ""), "повторный клик отклонён")

    # --- чёрные не могут ходить первыми
    wrong_turn = inline_cb(black, f"iq:{inline.token}:e7", msg)
    await bot.on_callback(wrong_turn)
    ok("ход соперника" in (wrong_turn.answers[-1][0] or ""), "очередь хода соблюдается")

    # --- белые: e2 -> e4
    e2 = inline_cb(white, f"iq:{inline.token}:e2", msg)
    await bot.on_callback(e2)
    ok(inline.selected.get(1) == "e2", "фигура выбрана")
    ok("Выбрана e2" in (e2.answers[-1][0] or ""), "подсказка о выборе")
    ok(client.edits[-1]["file"] is None, "при выборе меняются только кнопки")
    select_buttons = client.edits[-1]["buttons"]
    select_labels = [btn.text for row in select_buttons[:-2] for btn in row]
    ok(select_labels == ["e3", "e4"], "показаны только клетки для этой пешки")
    ok(select_buttons[-2][0].data == f"ic:{inline.token}".encode(),
       "есть кнопка вернуться к фигурам")

    # --- соперник не может сбросить чужой выбор
    back_other = inline_cb(black, f"ic:{inline.token}", msg)
    await bot.on_callback(back_other)
    ok("ход соперника" in (back_other.answers[-1][0] or ""),
       "чужой выбор не сбрасывается")
    ok(inline.selected.get(1) == "e2", "выбор белых остался")

    # --- передумал: назад к списку фигур
    back = inline_cb(white, f"ic:{inline.token}", msg)
    await bot.on_callback(back)
    ok(inline.selected.get(1) is None, "выбор снят кнопкой назад")
    back_labels = [btn.text for row in client.edits[-1]["buttons"][:-1] for btn in row]
    ok("a2 пешка" in back_labels, "снова показаны фигуры")

    e2_again = inline_cb(white, f"iq:{inline.token}:e2", msg)
    await bot.on_callback(e2_again)
    ok(inline.selected.get(1) == "e2", "фигура снова выбрана")

    e4 = inline_cb(white, f"iq:{inline.token}:e4", msg)
    await bot.on_callback(e4)
    ok(inline.game.board().piece_at(chess.E4) is not None, "ход e2-e4 сделан")
    ok(inline.game.board().turn == chess.BLACK, "ход перешёл чёрным")
    ok(not inline.selected, "выбор сброшен")
    ok("ход чёрных" in (client.edits[-1]["text"] or ""), "подпись обновилась")
    ok("e4" in (inline.game.last_move or ""), "последний ход запомнен")
    black_labels = [btn.text for row in client.edits[-1]["buttons"][:-1] for btn in row]
    ok("a7 пешка" in black_labels and "b8 конь" in black_labels,
       "чёрным показаны их фигуры")
    ok("a2 пешка" not in black_labels, "белые фигуры чёрным не показаны")

    twice = inline_cb(white, f"iq:{inline.token}:d2", msg)
    await bot.on_callback(twice)
    ok("ход соперника" in (twice.answers[-1][0] or ""), "белые ждут соперника")

    # --- чёрные: e7 -> e5
    for square in ("e7", "e5"):
        move = inline_cb(black, f"iq:{inline.token}:{square}", msg)
        await bot.on_callback(move)
    ok(inline.game.board().piece_at(chess.E5) is not None, "ответ чёрных")
    ok(inline.game.white_ms <= 300_000 and inline.game.black_ms <= 300_000,
       "часы идут у обоих")

    # --- ничья по договорённости
    draw = inline_cb(white, f"id:{inline.token}", msg)
    await bot.on_callback(draw)
    ok(inline.draw_offer_by == 1, "ничья предложена")
    accept = inline_cb(black, f"idy:{inline.token}", msg)
    await bot.on_callback(accept)
    ok(inline.game.is_finished, "ничья принята")
    ok("Ничья" in (client.edits[-1]["text"] or ""), "результат ничьей на доске")

    # --- сдача требует подтверждения
    msg2 = FakeInlineMessageId(2)
    token2 = invites.make_token(secret, 1, 5)
    join2 = inline_cb(black, f"ij:{token2}", msg2)
    await bot.on_callback(join2)
    inline2 = bot._inline_by_message[msg2._bytes()]

    ask = inline_cb(white, f"ir:{inline2.token}", msg2)
    await bot.on_callback(ask)
    ok(inline2.resign_pending_by == 1, "сдача требует подтверждения")
    wrong = inline_cb(black, f"iry:{inline2.token}", msg2)
    await bot.on_callback(wrong)
    ok("не твоя" in (wrong.answers[-1][0] or ""), "чужую сдачу не подтвердить")
    yes = inline_cb(white, f"iry:{inline2.token}", msg2)
    await bot.on_callback(yes)
    ok(inline2.game.is_finished, "сдача подтверждена")
    ok("сдался" in (client.edits[-1]["text"] or ""), "результат сдачи")

    # --- время вышло
    msg3 = FakeInlineMessageId(3)
    token3 = invites.make_token(secret, 1, 5)
    join3 = inline_cb(black, f"ij:{token3}", msg3)
    await bot.on_callback(join3)
    inline3 = bot._inline_by_message[msg3._bytes()]
    inline3.game.turn_started_at = time.time() - 301
    await bot.check_inline_clocks()
    ok(inline3.game.is_finished, "таймаут завершил партию")
    ok("Время вышло" in (client.edits[-1]["text"] or ""), "результат по времени")

    # --- если картинку в инлайн-сообщение вставить нельзя, шлём текстовую доску
    msg4 = FakeInlineMessageId(4)
    token4 = invites.make_token(secret, 1, 5)
    client.fail_photo_edits = True
    join4 = inline_cb(black, f"ij:{token4}", msg4)
    await bot.on_callback(join4)
    inline4 = bot._inline_by_message.get(msg4._bytes())
    ok(inline4 is not None, "партия началась и с текстовой доской")
    fallback = client.edits[-1]
    ok(fallback["file"] is None, "текстовый запасной вариант без картинки")
    ok("<pre>" in (fallback["text"] or ""), "доска нарисована текстом")
    ok("P P P P P P P P" in (fallback["text"] or ""), "белые пешки на месте")
    ok("r n b q k b n r" in (fallback["text"] or ""), "чёрные фигуры на месте")
    fallback_labels = [
        btn.text for row in fallback["buttons"][:-1] for btn in row
    ]
    ok("a2 пешка" in fallback_labels, "кнопки фигур остались")
    ok(len(fallback["buttons"]) >= 6, "список фигур и управление")
    ok("чёрными" in (join4.answers[-1][0] or ""), "цвет подтверждён")
    client.fail_photo_edits = False

    store.close()


async def test_time_warnings() -> None:
    """3 минуты — сопернику, 2 и 1 минута — всем участникам, без повторов."""
    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")

    game = await create_invite(bot, client, white, minutes=5)
    await accept_invite(bot, game, black)

    game = store.get(game.id)
    game.turn_started_at = time.time() - 125  # осталось ~2:55
    store.save(game)
    await bot.check_time_warnings()
    ok(any_text(client, "3 минуты", 1001), "белым видно предупреждение о 3 минутах")
    ok(any_text(client, "3 минуты", 1002), "соперник тоже видит предупреждение")
    sent = len(client.messages)

    await bot.check_time_warnings()
    ok(len(client.messages) == sent, "один и тот же порог не повторяется")

    game = store.get(game.id)
    game.turn_started_at = time.time() - 185  # осталось ~1:55
    store.save(game)
    await bot.check_time_warnings()
    ok(any_text(client, "2 минуты", 1001) and any_text(client, "2 минуты", 1002),
       "предупреждение о 2 минутах видно обоим")

    game = store.get(game.id)
    game.turn_started_at = time.time() - 245  # осталось ~0:55
    store.save(game)
    await bot.check_time_warnings()
    ok(any_text(client, "1 минута", 1001) and any_text(client, "1 минута", 1002),
       "предупреждение о 1 минуте видно обоим")

    game = store.get(game.id)
    ok(game.is_active, "предупреждения не завершают партию")
    store.close()


async def test_auto_cleanup() -> None:
    """Оконченные партии удаляются вместе с сообщениями-досками."""
    from chessbot import invites

    store = Store(":memory:")
    bot, client = await make_bot(store)
    white = user(1, "Вася")
    black = user(2, "Петя")

    game = await create_invite(bot, client, white, minutes=5)
    await accept_invite(bot, game, black)
    game = store.get(game.id)
    game.status = STATUS_FINISHED
    game.result_text = "тестовая партия окончена"
    store.save(game)
    ok(bool(game.msg_white and game.msg_black), "у партии есть обе доски")

    await bot.cleanup_finished_games(now=time.time())
    ok(store.get(game.id) is not None, "свежая оконченная партия ещё не удаляется")

    await bot.cleanup_finished_games(now=time.time() + 601)
    ok(store.get(game.id) is None, "старая оконченная партия удалена из базы")
    deleted = set(client.deletions)
    ok((game.white_chat, game.msg_white) in deleted, "доска белых удалена")
    ok((game.black_chat, game.msg_black) in deleted, "доска чёрных удалена")

    # инлайн-партия: сообщение Telegram удалить нельзя, но память и кнопки чистим
    secret = invites.make_secret("123:AA-test")
    inline_bot = ChessBot(client, store, inline_secret=secret)
    await inline_bot.setup()
    msg_id = FakeInlineMessageId(21)
    token = invites.make_token(secret, 1, 5)
    join = FakeCallbackEvent(
        1,
        black,
        f"ij:{token}",
        is_private=True,
        via_inline=True,
        inline_msg_id=msg_id,
    )
    await inline_bot.on_callback(join)
    inline = inline_bot._inline_by_message[msg_id._bytes()]
    inline.game.status = STATUS_FINISHED
    inline.game.result_text = "инлайн-партия окончена"
    await inline_bot._edit_inline(inline)
    ok(inline.finished_at is not None, "время окончания инлайн-партии запомнено")

    inline.finished_at = time.time() - 601
    await inline_bot.cleanup_finished_games(now=time.time())
    ok(inline.token not in inline_bot._inline_games,
       "оконченная инлайн-партия убрана из памяти")
    ok(msg_id._bytes() not in inline_bot._inline_by_message,
       "инлайн-сообщение больше не ищется по ключу")

    store.close()


async def test_inline_time_warnings() -> None:
    """В инлайн-партии предупреждение о времени видно в подписи доски."""
    from chessbot import invites

    store = Store(":memory:")
    client = FakeClient()
    secret = invites.make_secret("123:AA-test")
    bot = ChessBot(client, store, inline_secret=secret)
    await bot.setup()

    black = user(2, "Петя")
    msg_id = FakeInlineMessageId(31)
    token = invites.make_token(secret, 1, 5)
    join = FakeCallbackEvent(
        1,
        black,
        f"ij:{token}",
        is_private=True,
        via_inline=True,
        inline_msg_id=msg_id,
    )
    await bot.on_callback(join)
    inline = bot._inline_by_message[msg_id._bytes()]

    inline.game.turn_started_at = time.time() - 125  # осталось ~2:55
    await bot.check_inline_time_warnings()
    ok("3 минуты" in (inline.warning_text or ""), "предупреждение о 3 минутах")
    ok("3 минуты" in (client.edits[-1]["text"] or ""), "подпись доски обновилась")

    edits = len(client.edits)
    await bot.check_inline_time_warnings()
    ok(len(client.edits) == edits, "повторно то же предупреждение не шлётся")

    inline.game.turn_started_at = time.time() - 185  # осталось ~1:55
    await bot.check_inline_time_warnings()
    ok("2 минуты" in (inline.warning_text or ""), "предупреждение о 2 минутах")
    ok("2 минуты" in (client.edits[-1]["text"] or ""), "подпись обновилась на 2 минуты")

    inline.game.turn_started_at = time.time() - 245  # осталось ~0:55
    await bot.check_inline_time_warnings()
    ok("1 минута" in (inline.warning_text or ""), "предупреждение о 1 минуте")
    ok("1 минута" in (client.edits[-1]["text"] or ""), "подпись обновилась на 1 минуту")

    store.close()


async def main() -> None:
    tests = [
        test_private_invite_full_game,
        test_invite_declined_and_busy,
        test_group_flow_and_resign,
        test_timeout,
        test_cancel_waiting_and_group_abort,
        test_time_control_rejected_and_new_button,
        test_menu_buttons,
        test_inline_invite_flow,
        test_inline_board_flow,
        test_notify_falls_back_without_keyboard,
        test_time_warnings,
        test_auto_cleanup,
        test_inline_time_warnings,
    ]
    for test in tests:
        await test()
        print(f"  ✓ {test.__name__}")
    print(f"\nВсе сценарии пройдены: {PASSED} утверждений")


if __name__ == "__main__":
    asyncio.run(main())

