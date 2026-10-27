"""Тексты сообщений (русский язык, HTML-разметка Telegram)."""

from __future__ import annotations

import html

import chess

PIECE_RU = {
    chess.PAWN: "пешка",
    chess.KNIGHT: "конь",
    chess.BISHOP: "слон",
    chess.ROOK: "ладья",
    chess.QUEEN: "ферзь",
    chess.KING: "король",
}
SIDE_NOM = {chess.WHITE: "белые", chess.BLACK: "чёрные"}
SIDE_GEN = {chess.WHITE: "белых", chess.BLACK: "чёрных"}


def esc(value: str | None) -> str:
    return html.escape(value or "", quote=False)


def user_link(user_id: int | None, name: str | None) -> str:
    label = esc(name or "Игрок")
    if user_id is None:
        return label
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def mention_hint(username: str, example: str = "пешка e4") -> str:
    return f"<code>@{esc(username)} {esc(example)}</code>"


# -------------------------------------------------------------------- время
def format_clock(ms: int | None) -> str:
    if ms is None:
        return "—"
    total = max(0, int(ms)) // 1000
    if total >= 3600:
        hours, rest = divmod(total, 3600)
        return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def time_control_name(minutes: int) -> str:
    return f"{minutes} мин"


def time_control_line(game) -> str:
    if not game.timed:
        return "⏱ Без контроля времени"
    return f"⏱ По {time_control_name(game.base_ms // 60000)} каждому"


def clock_lines(game, board: chess.Board) -> list[str]:
    """Строки с часами для подписи доски."""
    if not game.timed:
        return []
    white = format_clock(game.remaining_ms(chess.WHITE))
    black = format_clock(game.remaining_ms(chess.BLACK))
    mark_white = "▶️ " if board.turn == chess.WHITE and game.is_active else ""
    mark_black = "▶️ " if board.turn == chess.BLACK and game.is_active else ""
    return [
        f"⚪ {user_link(game.white_id, game.white_name)} — "
        f"{mark_white}<b>{white}</b>",
        f"⚫ {user_link(game.black_id, game.black_name)} — "
        f"{mark_black}<b>{black}</b>",
    ]


def time_warning_text(game, color: chess.Color, minutes: int) -> str:
    """Предупреждение о том, что у игрока заканчивается время."""
    name = user_link(game.user_of(color), game.name_of(color))
    if minutes >= 3:
        return f"⏳ У {name} осталось <b>3 минуты</b>."
    if minutes == 2:
        return f"⚠️ У {name} осталось <b>2 минуты</b>."
    return f"🔥 У {name} осталась <b>1 минута</b>!"


def choose_time_text() -> str:
    return (
        "⏱ <b>Выбери контроль времени</b>\n\n"
        "Сколько минут даётся каждому игроку на всю партию? "
        "Время идёт только в свой ход."
    )


# --------------------------------------------------------------- приветствие
def start_text(username: str) -> str:
    return (
        "♟ <b>Шахматы прямо в чате</b>\n\n"
        "Внизу — меню с кнопками: <b>Новая партия</b>, <b>Доска</b>, "
        "<b>Ничья</b>, <b>Сдаться</b>, <b>Отменить</b>, <b>Помощь</b>.\n\n"
        "<b>Игра с другом в личке</b>\n"
        "1. Нажми «♟ Новая партия» — выбери время (5, 10 или 15 минут).\n"
        "2. Отправь другу ссылку-приглашение.\n"
        "3. Он нажимает «Принять» — и вы играете каждый в своём чате, "
        "а бот показывает доску обоим.\n\n"
        "<b>Ещё быстрее — без чата с ботом</b>\n"
        f"Набери <code>@{esc(username)}</code> в переписке с другом, выбери "
        "«Шахматы · 5/10/15 мин», друг нажмёт «Присоединиться» — доска "
        "появится прямо в этом сообщении, ходы — кнопками: фигура → клетка.\n\n"
        "<b>Ходы</b> — обычным текстом:\n"
        f"   • {mention_hint(username, 'пешка e4')}\n"
        f"   • {mention_hint(username, 'конь g1 f3')}\n"
        f"   • {mention_hint(username, 'рокировка')}\n"
        f"   • {mention_hint(username, 'пешка e7 e8 ферзь')} — превращение\n\n"
        "Если ход невозможен, бот объяснит почему.\n\n"
        "<b>Команды</b>\n"
        "/new — новая партия\n"
        "/board — показать доску\n"
        "/resign — сдаться (с подтверждением)\n"
        "/draw — предложить ничью\n"
        "/cancel — отменить партию\n"
        "/menu — показать кнопки\n"
        "/help — эта справка\n\n"
        "По другим вопросам: @sav3my"
    )


def menu_text(username: str) -> str:
    return (
        "📱 <b>Меню внизу</b>\n\n"
        "Нажимай кнопки или просто пиши ход:\n"
        f"{mention_hint(username, 'пешка e4')}\n\n"
        "Кнопки: Новая партия, Доска, Ничья, Сдаться, Отменить, Помощь."
    )


def menu_group_text() -> str:
    return (
        "📱 <b>Меню — в личке с ботом</b>\n\n"
        "В группе кнопки не показываются: напиши /new, /board, /draw, /resign или /cancel."
    )


def inline_invite_text(name: str, minutes: int, username: str) -> str:
    """Текст инлайн-результата (без HTML: уходит как обычное сообщение)."""
    return (
        f"♟ {name} приглашает в шахматы\n"
        f"⏱ {minutes} минут каждому\n\n"
        "Нажми «✅ Присоединиться» — партия начнётся.\n"
        "В личке доска появится прямо в этом сообщении, ходы — кнопками: фигура → клетка.\n"
        f"В группе бот @{username} должен быть участником чата."
    )


def inline_board_caption(game, board: chess.Board, username: str) -> str:
    """Подпись доски для игры в инлайн-сообщении (ходы кнопками)."""
    if game.status == "finished":
        return board_caption(game, board, username)

    lines = [f"♟ <b>Шахматы</b> · <b>ход {SIDE_GEN[board.turn]}</b>"]
    if game.timed:
        lines.extend(clock_lines(game, board))
    else:
        lines.extend(
            [
                f"⚪ {user_link(game.white_id, game.white_name)}",
                f"⚫ {user_link(game.black_id, game.black_name)}",
            ]
        )
    if game.last_move:
        lines.append(f"↩️ Последний ход: <code>{esc(game.last_move)}</code>")
    if board.is_check():
        lines.append("⚠️ <b>Шах!</b>")
    lines.append("")
    lines.append(
        "Ниже — только фигуры, у которых есть ходы. "
        "Выбери фигуру, затем клетку назначения."
    )
    return "\n".join(lines)


def inline_text_board(board: chess.Board) -> str:
    """Запасная текстовая доска, если картинку в инлайн-сообщение вставить нельзя."""
    letters = {
        chess.PAWN: "P",
        chess.KNIGHT: "N",
        chess.BISHOP: "B",
        chess.ROOK: "R",
        chess.QUEEN: "Q",
        chess.KING: "K",
    }
    rows = []
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            piece = board.piece_at(chess.square(file, rank))
            if piece is None:
                cells.append(".")
            else:
                letter = letters[piece.piece_type]
                cells.append(letter if piece.color == chess.WHITE else letter.lower())
        rows.append(f"{rank + 1}  " + " ".join(cells))
    rows.append("   a b c d e f g h")
    return "<pre>" + "\n".join(rows) + "</pre>"


def help_text(username: str) -> str:
    return (
        "<b>Примеры ходов</b>\n"
        f"• {mention_hint(username, 'пешка e4')} — пешка с e2 на e4\n"
        f"• {mention_hint(username, 'пешка e2 e4')} — то же, с указанием откуда\n"
        f"• {mention_hint(username, 'конь f3')} — если понятно, какой конь\n"
        f"• {mention_hint(username, 'конь g1 f3')} — если коней два\n"
        f"• {mention_hint(username, 'слон c1 g5')}\n"
        f"• {mention_hint(username, 'рокировка')} или {mention_hint(username, '0-0-0')}\n\n"
        "<b>Служебное</b>\n"
        "/new — новая партия (5, 10 или 15 минут)\n"
        "/board — доска\n"
        "/draw — ничья (нужно согласие соперника)\n"
        "/resign — сдаться (нужно подтверждение)\n"
        "/cancel — отменить партию\n\n"
        "<b>Другие вопросы</b>\n"
        "По другим вопросам: @sav3my"
    )


# -------------------------------------------------------------------- доска
def board_caption(game, board: chess.Board, username: str) -> str:
    white = user_link(game.white_id, game.white_name)
    black = user_link(game.black_id, game.black_name)
    control = time_control_line(game)

    if game.status == "waiting":
        if game.is_invite:
            link = invite_link(username, game.invite_token)
            return (
                "♟ <b>Шахматы</b>\n\n"
                f"⚪ Белые: {white}\n"
                f"{control}\n"
                "⏳ Ждём соперника.\n\n"
                "Отправь другу ссылку — он нажмёт «Принять»:\n"
                f"{esc(link)}\n\n"
                "Или нажми «📤 Поделиться приглашением»."
            )
        return (
            "♟ <b>Шахматы</b>\n\n"
            f"⚪ Белые: {white}\n"
            f"{control}\n"
            "⏳ Ждём соперника.\n\n"
            "Нажми <b>«Присоединиться»</b> или напиши "
            f"<code>@{esc(username)} присоединиться</code>."
        )

    if game.status == "finished":
        lines = [
            "🏁 <b>Партия окончена</b>\n",
            game.result_text or "Игра завершена.",
            "",
            f"⚪ {white}",
            f"⚫ {black}",
        ]
        if game.timed:
            lines.append(
                f"⏱ Остаток: {format_clock(game.white_ms)} — {format_clock(game.black_ms)}"
            )
        return "\n".join(lines)

    turn_side = SIDE_NOM[board.turn]
    mover = user_link(game.white_id if board.turn == chess.WHITE else game.black_id,
                      game.white_name if board.turn == chess.WHITE else game.black_name)

    lines = [f"♟ <b>Шахматы</b> · <b>ход {SIDE_GEN[board.turn]}</b>"]
    if game.timed:
        lines.extend(clock_lines(game, board))
    else:
        lines.extend([f"⚪ {white}", f"⚫ {black}"])
    if game.last_move:
        lines.append(f"↩️ Последний ход: <code>{esc(game.last_move)}</code>")
    if board.is_check():
        lines.append("⚠️ <b>Шах!</b>")
    lines.append("")
    lines.append(f"Ходит {mover}: {mention_hint(username)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- результаты
def result_checkmate(board: chess.Board, game) -> str:
    winner_color = not board.turn
    winner = user_link(
        game.white_id if winner_color == chess.WHITE else game.black_id,
        game.white_name if winner_color == chess.WHITE else game.black_name,
    )
    return f"🏆 <b>Мат!</b> Победа {SIDE_GEN[winner_color]} — {winner}."


def result_stalemate() -> str:
    return "🤷 <b>Пат.</b> Ходов нет, но шаха тоже нет — ничья."


def result_material() -> str:
    return "🤷 <b>Ничья:</b> недостаточно фигур для мата."


def result_repetition() -> str:
    return "🔁 <b>Ничья:</b> позиция повторилась слишком много раз."


def result_fifty() -> str:
    return "⏳ <b>Ничья:</b> 50 ходов без взятий и ходов пешками."


def result_resign(loser_id: int | None, loser_name: str | None, winner_color: chess.Color, game) -> str:
    winner = user_link(
        game.white_id if winner_color == chess.WHITE else game.black_id,
        game.white_name if winner_color == chess.WHITE else game.black_name,
    )
    return (
        f"🏳️ {user_link(loser_id, loser_name)} сдался(ась).\n"
        f"Победа {SIDE_GEN[winner_color]} — {winner}."
    )


def result_draw_agreement() -> str:
    return "🤝 <b>Ничья по договорённости.</b>"


def result_aborted() -> str:
    return "❌ <b>Партия отменена.</b>"


def result_solo_stop() -> str:
    return "🏁 <b>Партия завершена.</b>"


def result_timeout(loser_color: chess.Color, game) -> str:
    winner_color = not loser_color
    loser = user_link(game.user_of(loser_color), game.name_of(loser_color))
    winner = user_link(game.user_of(winner_color), game.name_of(winner_color))
    return (
        f"⌛ <b>Время вышло</b> у {loser}.\n"
        f"Победа {SIDE_GEN[winner_color]} — {winner}."
    )


# ------------------------------------------------------------------ приглашения
def invite_link(username: str, token: str | None) -> str:
    if not token:
        return ""
    return f"https://t.me/{username}?start=g_{token}"


def share_link(username: str, token: str | None) -> str:
    from urllib.parse import quote

    invite = invite_link(username, token)
    text = quote("Сыграем в шахматы? Открой ссылку и нажми «Принять».")
    return f"https://t.me/share/url?url={quote(invite, safe='')}&text={text}"


def invite_offer_text(inviter: str, game) -> str:
    return (
        "♟ <b>Приглашение в шахматы</b>\n\n"
        f"{inviter} зовёт тебя сыграть.\n"
        f"{time_control_line(game)}\n\n"
        "Ты будешь играть чёрными. Принять?"
    )


def invite_declined_text(name: str) -> str:
    return f"🙅 {esc(name)} отклонил(а) приглашение. Приглашение всё ещё активно."


def invite_accepted_text(game) -> str:
    return (
        "▶️ <b>Партия началась!</b>\n"
        f"⚪ Белые: {user_link(game.white_id, game.white_name)}\n"
        f"⚫ Чёрные: {user_link(game.black_id, game.black_name)}\n"
        f"{time_control_line(game)}\n\n"
        "Первыми ходят белые."
    )


def invite_invalid_text() -> str:
    return "😕 Приглашение недействительно или уже неактуально. Создай свою партию: /new"


def invite_own_text() -> str:
    return "🙂 Это твоё собственное приглашение. Отправь ссылку другу."


def invite_busy_text() -> str:
    return "⚠️ У тебя уже есть незавершённая партия. Заверши её (/cancel) и попробуй снова."


# ------------------------------------------------------------------ подсказки
def resign_confirm_text(link: str) -> str:
    return (
        f"🏳️ {link}, точно сдаёшься?\n\n"
        "Партия закончится, победа достанется сопернику."
    )


def draw_offer_text(link: str) -> str:
    return f"🤝 {link} предлагает ничью.\n\nСоперник, согласен?"


def _hints_line(hints: list[str]) -> str:
    if not hints:
        return ""
    if len(hints) > 6:
        hints = hints[:6] + ["…"]
    return "\nВозможные ходы: " + ", ".join(f"<code>{esc(h)}</code>" for h in hints)


def move_error_text(code: str, data: dict) -> str:
    if code == "no_square":
        return (
            "🤔 Не понял ход — не вижу клетки.\n"
            "Пример: <code>пешка e4</code>, <code>конь g1 f3</code>."
        )
    if code == "too_many_squares":
        return "🤔 Слишком много клеток в сообщении. Укажи откуда и куда: <code>конь g1 f3</code>."

    if code == "no_piece":
        return f"❌ На клетке <b>{esc(data.get('square'))}</b> нет фигуры."

    if code == "enemy_piece":
        return f"❌ На клетке <b>{esc(data.get('square'))}</b> стоит фигура соперника."

    if code == "wrong_piece":
        return (
            f"❌ На клетке <b>{esc(data.get('square'))}</b> стоит "
            f"{esc(data.get('actual'))}, а не {esc(data.get('wanted'))}."
        )

    if code == "own_target":
        return f"❌ На клетке <b>{esc(data.get('square'))}</b> стоит своя фигура."

    if code == "ambiguous":
        options = "\n".join(f"• <code>{esc(opt)}</code>" for opt in data.get("options", []))
        return f"🤔 Непонятно, какой фигурой идти. Уточни:\n{options}"

    if code == "castle_impossible":
        reason = data.get("reason")
        if reason == "check":
            return "❌ Рокировка невозможна: король под шахом."
        if reason == "attacked":
            return "❌ Рокировка невозможна: король проходит через битое поле или оно занято."
        return "❌ Рокировка сейчас невозможна: потеряно право на рокировку."

    if code == "illegal":
        piece = data.get("piece")
        target = data.get("target")
        head = f"❌ {piece.capitalize() if piece else 'Так'} "
        head += f"не может так сходить на <b>{esc(target)}</b>." if target else "так не ходит."
        hints = _hints_line(data.get("hints") or [])
        if data.get("pinned"):
            return head + "\nФигура прикрывает короля — сначала убери фигуру с линии." + hints
        if data.get("castle_failed"):
            return head + "\nРокировка сейчас невозможна (права потеряны, шах или битое поле)."
        return head + hints

    return "❌ Так ходить нельзя."


def not_started_text(username: str) -> str:
    return (
        "Сейчас нет активной партии.\n"
        "Создай новую: <code>/new</code> — выберешь время и получишь приглашение для друга."
    )


def waiting_text() -> str:
    return "⏳ Партия ещё не началась — ждём, пока соперник примет приглашение."


def join_private_text() -> str:
    return (
        "🙂 В личных сообщениях к чужой партии не присоединиться.\n"
        "Попроси у друга ссылку-приглашение (он получит её после /new)."
    )


def finished_text() -> str:
    return "🏁 Эта партия уже окончена. Напиши /new, чтобы начать новую."


def not_player_text() -> str:
    return "🙅 Ты не участвуешь в этой партии."


def wait_your_opponent_text(game, color: chess.Color) -> str:
    opponent = user_link(game.user_of(not color), game.name_of(not color))
    return f"⏳ Ты уже сходил(а). Ждём хода соперника — {opponent}."


def your_turn_text(game, last_move: str | None) -> str:
    lines = ["♟ <b>Твой ход!</b>"]
    if last_move:
        lines.append(f"Соперник сходил: <code>{esc(last_move)}</code>")
    if game.timed:
        color = game.board().turn
        lines.append(f"⏱ У тебя осталось: <b>{format_clock(game.remaining_ms(color))}</b>")
    lines.append("")
    lines.append("Напиши свой ход, например: <code>пешка e5</code>")
    return "\n".join(lines)


def not_your_turn_text(board: chess.Board, game) -> str:
    if board.turn == chess.WHITE:
        who = user_link(game.white_id, game.white_name)
    else:
        who = user_link(game.black_id, game.black_name)
    return f"⏳ Сейчас ход {SIDE_GEN[board.turn]} — {who}. Ты сходишь после него."


def already_active_text() -> str:
    return "⚠️ В этом чате уже идёт партия. Заверши её (/resign) или отмени (/cancel)."


def already_waiting_text() -> str:
    return "⏳ Партия уже создана, ждём соперника."


def game_started_text(game) -> str:
    return (
        "▶️ <b>Партия началась!</b>\n"
        f"⚪ Белые: {user_link(game.white_id, game.white_name)}\n"
        f"⚫ Чёрные: {user_link(game.black_id, game.black_name)}\n"
        f"{time_control_line(game)}\n\n"
        "Первыми ходят белые."
    )


def draw_declined_text(link: str) -> str:
    return f"🙅 {link} отклонил(а) ничью. Играем дальше."


def draw_cancelled_text(link: str) -> str:
    return f"↩️ {link} отозвал(а) предложение ничьей."


def resign_cancelled_text(link: str) -> str:
    return f"👍 {link} передумал(а) сдаваться. Играем дальше."


def not_your_confirmation() -> str:
    return "Это подтверждение не для тебя 🙂"
