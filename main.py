from __future__ import annotations

import asyncio
import io
import sys

from src.browser import open_tiktok
from src.flow import run as run_parser

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    if getattr(sys.stdout, "encoding", None) != "utf-8":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )


def _log(msg: str) -> None:
    print(f"[main] {msg}")


async def main() -> None:
    handle = None
    page = None
    playwright = None

    print()
    print("=" * 60)
    print("TikTok parser")
    print("=" * 60)
    print()
    print("Если Chrome уже с портом 9222 — подключусь к нему.")
    print("Если нет — сам открою Chrome; профиль в папке .tiktok_chrome_profile (логин сохранится).")
    print("=" * 60)
    print()

    try:
        _log("запуск браузера...")
        handle, page, playwright = await open_tiktok()
        _log("страница готова")

        print()
        print("В окне Chrome: открой ленту TikTok и убедись, что всё залогинено.")
        print("В терминале: нажми Enter — начнётся скролл и парсинг.")
        print()
        try:
            input(">>> Enter для старта парсинга ")
        except EOFError:
            _log("нет интерактивного ввода (EOF) — продолжаю")

        _log("запуск оркестратора")
        await run_parser(page)

        print()
        print("=" * 60)
        print("Результат")
        print("=" * 60)

        print()
        try:
            input(">>> Enter для выхода ")
        except EOFError:
            pass

    except Exception as e:
        print()
        print(f"[main] Ошибка: {e}")
        print()

    finally:
        _log("отключение Playwright (Chrome можно не закрывать)")
        if handle is not None:
            try:
                await handle.close()
            except Exception as e:
                _log(f"handle.close: {e}")
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as e:
                _log(f"playwright.stop: {e}")
        print()
        _log("конец")
        print()


if __name__ == "__main__":
    asyncio.run(main())
