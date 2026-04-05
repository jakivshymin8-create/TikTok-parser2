"""
Оркестратор режимов парсинга.

Порядок:
  1. Инициализация БД (accounts.db)
  2. SEARCH  — поиск по запросам (search_mode.py)
  3. FEED    — бесконечная лента For You (scroll.py)
"""

import asyncio

from src.scroll import human_scroll
from src.search_mode import run_search_mode
from src.database import init_db, print_stats


def _log(msg: str) -> None:
    print(f"[orchestrator] {msg}")


ENABLE_SEARCH_MODE = False
ENABLE_FEED_AFTER_SEARCH = True  # всегда True — только лента For You


async def run(page) -> None:
    _log("Запуск оркестратора")

    # Инициализация БД — создаёт таблицы если не существуют
    init_db()
    _log("БД инициализирована")
    print_stats()

    _log(f"  search_mode: {ENABLE_SEARCH_MODE}")
    _log(f"  feed_after_search: {ENABLE_FEED_AFTER_SEARCH}")

    if ENABLE_SEARCH_MODE:
        _log("\n" + "═" * 60)
        _log("РЕЖИМ: ПОИСК ПО ЗАПРОСАМ")
        _log("═" * 60)
        try:
            await run_search_mode(page)
        except Exception as e:
            _log(f"Ошибка в режиме поиска: {e}")
            _log("Переключаюсь в режим ленты")

        # Статистика после поиска
        print()
        print_stats()

    if ENABLE_FEED_AFTER_SEARCH or not ENABLE_SEARCH_MODE:
        _log("\n" + "═" * 60)
        _log("РЕЖИМ: ЛЕНТА FOR YOU")
        _log("═" * 60)
        try:
            await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(3)
            _log("Возврат на главную ✓")
        except Exception as e:
            _log(f"Ошибка возврата на главную: {e}")

        try:
            await human_scroll(page)
        except Exception as e:
            _log(f"Ошибка в режиме ленты: {e}")

    _log("Оркестратор завершён")
    print_stats()
