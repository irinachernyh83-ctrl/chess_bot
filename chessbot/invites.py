"""Подписанные приглашения для инлайн-режима.

Формат не хранится в базе: в кнопке лежит подписанный токен с id приглашающего
и контролем времени, поэтому приглашение переживает перезапуск бота.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

ALLOWED_MINUTES = (5, 10, 15)
TTL_SECONDS = 24 * 60 * 60


def make_secret(bot_token: str) -> bytes:
    return hashlib.sha256(("chess-inline:" + bot_token).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(secret: bytes, white_id: int, minutes: int, now: float | None = None) -> str:
    if minutes not in ALLOWED_MINUTES:
        raise ValueError("неподдерживаемый контроль времени")
    payload = f"{int(white_id)}:{int(minutes)}:{int(now if now is not None else time.time())}"
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:10]
    return _b64(f"{payload}.{signature}".encode())


def parse_token(
    secret: bytes,
    token: str,
    now: float | None = None,
    ttl: int = TTL_SECONDS,
) -> tuple[int, int] | None:
    """Возвращает (white_id, minutes) или None, если токен битый/просрочен."""
    try:
        raw = _unb64(token).decode()
        payload, signature = raw.rsplit(".", 1)
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:10]
        if not hmac.compare_digest(signature, expected):
            return None
        white_text, minutes_text, stamp_text = payload.split(":")
        minutes = int(minutes_text)
        if minutes not in ALLOWED_MINUTES:
            return None
        if (now if now is not None else time.time()) - int(stamp_text) > ttl:
            return None
        return int(white_text), minutes
    except (ValueError, UnicodeDecodeError):
        return None
