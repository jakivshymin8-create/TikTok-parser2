"""
Модуль поиска TikTok по заданным запросам.
Вызывается из orchestrator.py.

Тематика: деньги, заработок, крипта, схемы, бизнес, саморазвитие (UA/RU).
"""

import asyncio
import datetime
import random

from src.profile import analyze_one_video
from src.page_utils import _ensure_muted, ensure_video_playing
from src.database import is_checked

SEARCH_BASE = "https://www.tiktok.com/search/video?q="

_YEAR = datetime.date.today().year

SEARCH_QUERIES = [
    # Деньги и заработок
    f"заработок {_YEAR}",
    f"заробіток {_YEAR}",
    f"як заробити гроші {_YEAR}",
    f"как заработать деньги {_YEAR}",
    "заработок в интернете",
    "заробіток в інтернеті",
    "пассивный доход",
    "пасивний дохід",

    # Крипта
    f"крипта {_YEAR}",
    f"крипто заработок {_YEAR}",
    "криптовалюта заработок",
    "криптовалюта дохід",

    # Схемы и бизнес
    "схема заработка",
    "схема заробітку",
    f"бизнес с нуля {_YEAR}",
    f"бізнес з нуля {_YEAR}",
    "арбитраж трафика",
    "арбітраж трафіку",

    # Саморазвитие
    f"саморазвитие {_YEAR}",
    f"саморозвиток {_YEAR}",
    "мотивация деньги",
    "мотивація гроші",
    "успех мышление",
    "успіх мислення",
    "дисциплина успех",
    "дисципліна успіх",
]

# Сессионная дедупликация
_session_seen: set[str] = set()


def _log(msg: str) -> None:
    print(f"[search_mode] {msg}")


def _username_from_href(href: str) -> str | None:
    """Извлекает username из href вида /@username/video/ID."""
    try:
        if "/@" in href and "/video/" in href:
            return href.split("/@")[1].split("/video/")[0]
    except Exception:
        pass
    return None


async def navigate_to_search(page, query: str) -> str | None:
    """Переходит на страницу поиска. publishTime=90 → последние 3 месяца."""
    encoded = query.replace(" ", "%20")
    url = f"{SEARCH_BASE}{encoded}&publishTime=90"
    _log(f"Поиск: '{query}' → {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(4)
        _log("Страница поиска загружена")
        return page.url
    except Exception as e:
        _log(f"Ошибка перехода к поиску: {e}")
        return None


async def _zoom_page(page, zoom: float) -> None:
    try:
        await page.evaluate(f"document.body.style.zoom = '{zoom}'")
    except Exception as e:
        _log(f"Ошибка зума: {e}")


async def collect_video_links(page) -> list[str]:
    """Собирает ссылки на видео формата /@username/video/ID."""
    try:
        hrefs = await page.evaluate("""
            () => {
                const results = [];
                const containers = [
                    document.querySelector('[class*="DivSearchResultContainer"]'),
                    document.querySelector('[class*="search-result"]'),
                    document.querySelector('[class*="SearchResult"]'),
                    document.querySelector('main'),
                    document.body,
                ];
                let container = null;
                for (const c of containers) {
                    if (c) { container = c; break; }
                }
                if (!container) return [];
                const links = Array.from(container.querySelectorAll('a[href*="/video/"]'));
                for (const a of links) {
                    const href = a.getAttribute('href');
                    if (!href) continue;
                    if (!href.match(/\\/@[^/]+\\/video\\/\\d+/)) continue;
                    results.push(href);
                }
                return [...new Set(results)];
            }
        """)
        return hrefs or []
    except Exception as e:
        _log(f"Ошибка сбора ссылок: {e}")
        return []


async def scroll_and_collect(page, target_count: int = 40) -> list[str]:
    """
    Зумируем до 67% → собираем ссылки скроллом → восстанавливаем зум.
    Пропускаем ссылки уже проверенных аккаунтов на этапе сбора.
    """
    _log("Зум 67% для максимального сбора ссылок...")
    await _zoom_page(page, 0.67)
    await asyncio.sleep(1)

    all_hrefs: list[str] = []
    seen_hrefs: set[str] = set()
    no_new = 0

    while len(all_hrefs) < target_count:
        batch = await collect_video_links(page)
        new = [h for h in batch if h not in seen_hrefs]

        if not new:
            no_new += 1
            if no_new >= 8:
                _log("Новых ссылок нет → стоп сбора")
                break
        else:
            no_new = 0
            for h in new:
                seen_hrefs.add(h)
                uname = _username_from_href(h)
                if uname and (uname in _session_seen or is_checked(uname)):
                    _log(f"@{uname} уже проверен → пропуск при сборе")
                    continue
                all_hrefs.append(h)
            _log(f"Собрано ссылок: {len(all_hrefs)}")

        if len(all_hrefs) >= target_count:
            break

        scroll_amount = random.randint(1500, 2500)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(2.5)

    _log("Восстанавливаю зум 100%")
    await _zoom_page(page, 1.0)
    await asyncio.sleep(0.5)

    return all_hrefs[:target_count]


async def scroll_search_results(page, search_url: str, max_videos: int = 40) -> None:
    """Главный цикл: сбор ссылок → анализ каждого видео → возврат на поиск."""
    _log(f"Сбор ссылок для {max_videos} видео...")
    hrefs = await scroll_and_collect(page, target_count=max_videos)

    if not hrefs:
        _log("Ссылки не найдены → пропускаем запрос")
        return

    _log(f"Начинаю анализ {len(hrefs)} видео")

    for idx, href in enumerate(hrefs):
        video_url = f"https://www.tiktok.com{href}" if href.startswith("/") else href

        uname = _username_from_href(href)
        if uname and (uname in _session_seen or is_checked(uname)):
            _log(f"@{uname} уже проверен → пропуск")
            continue

        _log(f"\nВидео {idx + 1}/{len(hrefs)}: {video_url}")

        try:
            await page.goto(video_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)
            await _ensure_muted(page)
            await ensure_video_playing(page)

            await analyze_one_video(page, return_url=search_url)

            if uname:
                _session_seen.add(uname)

        except Exception as e:
            _log(f"Ошибка обработки: {e}")

        finally:
            # Возвращаемся на поиск только если страница ушла (similar мог вернуть сам)
            if search_url not in page.url:
                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
                    await asyncio.sleep(2)
                except Exception as e:
                    _log(f"Ошибка возврата на поиск: {e}")

        await asyncio.sleep(random.uniform(1, 2.5))

    _log(f"Запрос завершён. Обработано: {len(hrefs)} видео")


async def run_search_mode(page) -> None:
    """Точка входа. Вызывается из orchestrator.py."""
    _log(f"Запуск режима поиска. Запросов: {len(SEARCH_QUERIES)}")
    _session_seen.clear()

    for idx, query in enumerate(SEARCH_QUERIES):
        _log(f"\n{'='*50}")
        _log(f"Запрос {idx + 1}/{len(SEARCH_QUERIES)}: '{query}'")
        _log(f"{'='*50}")

        search_url = await navigate_to_search(page, query)
        if not search_url:
            _log("Не удалось открыть поиск → следующий запрос")
            continue

        search_url = page.url
        await scroll_search_results(page, search_url=search_url, max_videos=40)

        pause = random.uniform(3, 6)
        _log(f"Пауза {pause:.1f}с перед следующим запросом")
        await asyncio.sleep(pause)

    _log(f"\nВсе запросы завершены. Сессия: обработано {len(_session_seen)} уникальных аккаунтов")
