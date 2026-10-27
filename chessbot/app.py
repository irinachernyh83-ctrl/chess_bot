"""Сборка и запуск приложения (Telethon / MTProto)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from urllib.parse import unquote, urlsplit

from telethon import TelegramClient
from telethon.network import ConnectionTcpFull, ConnectionTcpMTProxyRandomizedIntermediate
from telethon.sessions import SQLiteSession

from . import invites
from .handlers import register
from .store import Store

log = logging.getLogger(__name__)

# публичные ключи Telegram Desktop — их достаточно для работы бота через MTProto
DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


def _parse_mtproxy(value: str) -> tuple[str, int, str]:
    parts = value.strip().split(":")
    if len(parts) < 3:
        raise SystemExit("MTPROXY должен быть в формате host:port:secret")
    host = parts[0]
    try:
        port = int(parts[1])
    except ValueError as exc:
        raise SystemExit("MTPROXY: порт должен быть числом") from exc
    return host, port, parts[2]


def _proxy_from_url(url: str) -> dict:
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("socks5", "socks4", "http"):
        raise SystemExit("PROXY_URL: поддерживаются socks5://, socks4://, http://")
    return {
        "proxy_type": scheme,
        "addr": parts.hostname,
        "port": parts.port,
        "username": unquote(parts.username) if parts.username else None,
        "password": unquote(parts.password) if parts.password else None,
        "rdns": True,
    }


def _health_port() -> int | None:
    """Порт для HTTP-проверки (Render, Koyeb и другие PaaS требуют слушать $PORT)."""
    raw = (os.getenv("HEALTH_PORT") or os.getenv("PORT") or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit("HEALTH_PORT/PORT должен быть числом") from exc
    if not 0 < port < 65536:
        raise SystemExit("HEALTH_PORT/PORT должен быть в диапазоне 1..65535")
    return port


def _start_health_server(port: int) -> None:
    """Минимальный HTTP-ответ «ok», чтобы хостинг считал сервис живым."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — имя задаёт http.server
            self._reply(b"ok\n")

        def do_HEAD(self) -> None:  # noqa: N802
            self._reply(b"")

        def log_message(self, fmt: str, *args) -> None:
            log.debug("health: " + fmt, *args)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    log.info("Health-сервер: http://0.0.0.0:%s/", port)


async def _run(
    token: str,
    db_path: str,
    session_path: str,
    api_id: int,
    api_hash: str,
    mtproxy: str | None,
    proxy_url: str | None,
    finished_ttl: float,
) -> None:
    kwargs: dict = {"connection": ConnectionTcpFull}

    if mtproxy:
        host, port, secret = _parse_mtproxy(mtproxy)
        kwargs["connection"] = ConnectionTcpMTProxyRandomizedIntermediate
        kwargs["proxy"] = (host, port, secret)
        log.info("MTProto-прокси: %s:%s", host, port)
    elif proxy_url:
        kwargs["proxy"] = _proxy_from_url(proxy_url)
        log.info("SOCKS/HTTP-прокси: %s", urlsplit(proxy_url).scheme)

    os.makedirs(os.path.dirname(os.path.abspath(session_path)) or ".", exist_ok=True)

    health_port = _health_port()
    if health_port:
        _start_health_server(health_port)

    client = TelegramClient(
        SQLiteSession(session_path),
        api_id,
        api_hash,
        device_model="ChessBot",
        system_version="1.0",
        app_version="1.0",
        **kwargs,
    )

    await client.start(bot_token=token)
    store = Store(db_path)
    bot = register(
        client,
        store,
        inline_secret=invites.make_secret(token),
        finished_ttl=finished_ttl,
    )
    await bot.setup()
    bot.start_clock_watch()
    log.info("База партий: %s", os.path.abspath(db_path))

    try:
        await client.run_until_disconnected()
    finally:
        await bot.stop()
        store.close()
        await client.disconnect()


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)

    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token and len(sys.argv) > 1:
        token = sys.argv[1].strip()
    if not token:
        sys.exit(
            "Не задан токен бота.\n"
            "Создай .env (см. .env.example) и укажи BOT_TOKEN=...\n"
            "или запусти: python main.py 123456:AA..."
        )

    try:
        api_id = int(os.getenv("API_ID", DEFAULT_API_ID))
    except ValueError:
        sys.exit("API_ID должен быть числом")

    api_hash = (os.getenv("API_HASH") or DEFAULT_API_HASH).strip()
    db_path = os.getenv("DB_PATH", os.path.join("data", "chess.db"))
    session_path = os.getenv("SESSION_PATH", os.path.join("data", "chessbot"))
    mtproxy = (os.getenv("MTPROXY") or "").strip() or None
    proxy_url = (os.getenv("PROXY_URL") or "").strip() or None

    try:
        finished_ttl = float(os.getenv("FINISHED_GAME_TTL", "600"))
    except ValueError:
        sys.exit("FINISHED_GAME_TTL должен быть числом секунд (0 — не удалять)")

    try:
        asyncio.run(
            _run(
                token,
                db_path,
                session_path,
                api_id,
                api_hash,
                mtproxy,
                proxy_url,
                finished_ttl,
            )
        )
    except KeyboardInterrupt:
        log.info("Остановлено пользователем")


if __name__ == "__main__":
    main()
