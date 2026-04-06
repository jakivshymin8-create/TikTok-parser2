"""
Модуль анализа похожих аккаунтов TikTok.
Вызывается из profile_pipeline.py после успешного сохранения трафера.
Берёт первые 5 похожих аккаунтов со страницы профиля и прогоняет через analyze_one_video.
Возвращает управление на исходный URL (search / feed / etc).
"""

import asyncio

from src.database import is_checked

SIMILAR_COUNT = 5

_SIMILAR_SELECTORS = [
    '[data-e2e="suggest-user-card"] a[href*="/@"]',
    '[class*="SuggestUser"] a[href*="/@"]',
    '[class*="suggest"] a[href*="/@"]',
    '[class*="Recommend"] a[href*="/@"]',
    'section a[href*="/@"]',
]


def _log(msg: str) -> None:
    print(f"[similar] {msg}")


async def _collect_similar_usernames(page) -> list[str]:
    """
    Собирает username похожих аккаунтов со страницы профиля.
    Возвращает список уникальных username (без @).
    """
    usernames: list[str] = []
    seen: set[str] = set()

    for sel in _SIMILAR_SELECTORS:
        try:
            elements = page.locator(sel)
            count = await asyncio.wait_for(elements.count(), timeout=3)
            if count == 0:
                continue

            _log(f"Найден блок похожих ({sel}): {count} элементов")
            for i in range(min(count, SIMILAR_COUNT * 2)):
                try:
                    href = await elements.nth(i).get_attribute("href")
                    if not href or "/@" not in href:
                        continue
                    uname = href.split("/@")[1].split("/")[0].split("?")[0]
                    if uname and uname not in seen and len(uname) > 1:
                        seen.add(uname)
                        usernames.append(uname)
                        if len(usernames) >= SIMILAR_COUNT:
                            break
                except Exception:
                    continue

            if usernames:
                break
        except Exception:
            continue

    # Fallback: любые ссылки на профили на странице
    if not usernames:
        try:
            _log("Основные селекторы не дали результата → fallback")
            hrefs = await page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/@"]'));
                    const results = [];
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        if (!href.startsWith('/@')) continue;
                        if (href.includes('/video/')) continue;
                        const uname = href.replace('/@', '').split('/')[0].split('?')[0];
                        if (uname && uname.length > 1) results.push(uname);
                    }
                    return [...new Set(results)];
                }
            """)
            for uname in (hrefs or []):
                if uname not in seen:
                    seen.add(uname)
                    usernames.append(uname)
                if len(usernames) >= SIMILAR_COUNT:
                    break
        except Exception as e:
            _log(f"Fallback ошибка: {e}")

    return usernames[:SIMILAR_COUNT]


async def analyze_similar_accounts(page, return_url: str) -> None:
    """
    Главная функция. Вызывается со страницы профиля трафера.
    После обработки всех похожих — ОБЯЗАТЕЛЬНО возвращает на return_url.

    Параметры:
        page       — текущая страница Playwright
        return_url — URL для возврата после анализа (search_url / FEED_URL / etc)
    """
    # Локальный импорт — избегаем circular import
    # (similar → profile_pipeline → similar — circular, но local import разрывает цикл)
    from src.profile import analyze_one_video
    from src.page_utils import _ensure_muted, ensure_video_playing

    _log("Ищу похожие аккаунты на странице профиля...")
    
    # Запоминаем откуда пришли
    original_url = return_url
    _log(f"Запомнил URL возврата: {original_url}")

    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)
        usernames = await _collect_similar_usernames(page)
    except Exception as e:
        _log(f"Ошибка сбора похожих: {e} → возврат")
        await _safe_return(page, original_url)
        return

    if not usernames:
        _log("Похожие аккаунты не найдены → возврат")
        await _safe_return(page, original_url)
        return

    _log(f"Найдено {len(usernames)} похожих: {usernames}")

    for idx, uname in enumerate(usernames):
        _log(f"\nПохожий {idx + 1}/{len(usernames)}: @{uname}")

        if is_checked(uname):
            _log(f"@{uname} уже проверен → пропуск")
            continue

        profile_url = f"https://www.tiktok.com/@{uname}"
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)

            first_video = page.locator('[data-e2e="user-post-item"] a').first
            video_href = await asyncio.wait_for(
                first_video.get_attribute("href"), timeout=5000
            )

            if not video_href:
                _log(f"@{uname}: нет видео → пропуск")
                continue

            video_url = (
                f"https://www.tiktok.com{video_href}"
                if video_href.startswith("/")
                else video_href
            )

            _log(f"@{uname}: открываю первое видео → {video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)
            await _ensure_muted(page)
            await ensure_video_playing(page)

            # _depth=1 → внутри analyze_one_video шаг похожих не запустится
            # return_url НЕ передаём — analyze_one_video сам не должен возвращаться
            _log(f"@{uname}: запускаю analyze_one_video с _depth=1")
            try:
                result = await analyze_one_video(page, return_url="", _depth=1)
                if result:
                    _log(f"@{uname}: анализ завершён успешно")
                else:
                    _log(f"@{uname}: анализ завершён (не трафер)")
            except Exception as e:
                _log(f"@{uname}: ошибка в analyze_one_video: {e}")

        except Exception as e:
            _log(f"@{uname}: ошибка обработки: {e} → следующий")
            continue

        await asyncio.sleep(1.5)

    _log("Анализ похожих завершён → ОБЯЗАТЕЛЬНЫЙ возврат")
    await _safe_return(page, original_url)


async def _safe_return(page, return_url: str) -> None:
    """Безопасно возвращает на return_url. КРИТИЧНО: всегда должен выполниться."""
    if not return_url:
        _log("⚠️  return_url пустой — возврат невозможен")
        return
    
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            _log(f"Возврат на: {return_url} (попытка {attempt + 1}/{max_attempts})")
            await page.goto(return_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)
            
            # Проверяем что действительно вернулись
            current_url = page.url
            if return_url in current_url or current_url in return_url:
                _log(f"✓ Возврат успешен: {current_url}")
                return
            else:
                _log(f"⚠️  URL не совпадает: ожидали {return_url}, получили {current_url}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2)
                    continue
        except Exception as e:
            _log(f"Ошибка возврата (попытка {attempt + 1}): {e}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(3)
            else:
                _log("⚠️  Не удалось вернуться после всех попыток")
    
    _log("⚠️  Возврат не удался, но продолжаем работу")
