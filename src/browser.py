"""
Два режима (автоматически):
1) CDP: уже запущенный Chrome с --remote-debugging-port → подключаемся (твой профиль, если так запустил).
2) Иначе: Playwright поднимает Chrome с отдельным профилем в .tiktok_chrome_profile (логин один раз — дальше сохраняется).
"""

from __future__ import annotations

import asyncio
import os
import urllib.error
import urllib.request
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

CDP_URL = os.environ.get("TIKTOK_CDP_URL", "http://127.0.0.1:9222")
CONNECT_TIMEOUT_S = 45.0
TIKTOK_URL = "https://www.tiktok.com/"

_PROFILE_DIR = Path(__file__).resolve().parent.parent / ".tiktok_chrome_profile"

# ── JS: глобальный мут — IIFE, чтобы add_init_script реально выполнял код ─────
# КРИТИЧНО: звук НИКОГДА не должен включаться
_MUTE_INIT_SCRIPT = """
(function() {
    // Перехватываем play() — всегда muted
    const origPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function() {
        this.muted = true;
        this.volume = 0;
        return origPlay.apply(this, arguments);
    };
    
    // Блокируем изменение volume — всегда 0
    const origVolDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'volume');
    Object.defineProperty(HTMLMediaElement.prototype, 'volume', {
        set: function() { origVolDesc.set.call(this, 0); },
        get: function() { return 0; },
        configurable: true,
    });
    
    // Блокируем изменение muted — всегда true
    const origMutedDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'muted');
    Object.defineProperty(HTMLMediaElement.prototype, 'muted', {
        set: function() { origMutedDesc.set.call(this, true); },
        get: function() { return true; },
        configurable: true,
    });
    
    // Мутим все существующие и новые элементы
    const muteAll = function() {
        document.querySelectorAll('video, audio').forEach(function(el) {
            el.muted = true;
            el.volume = 0;
        });
    };
    
    // MutationObserver для автоматического мута новых элементов
    const obs = new MutationObserver(muteAll);
    obs.observe(document.documentElement, { childList: true, subtree: true });
    
    // Мутим сразу
    muteAll();
    
    // Периодическая проверка каждые 500ms (защита от багов)
    setInterval(muteAll, 500);
})();
"""


def _cdp_json_reachable() -> bool:
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def chrome_launch_instructions() -> str:
    local = os.environ.get("LOCALAPPDATA", r"%LOCALAPPDATA%")
    user_data = os.path.join(local, "Google", "Chrome", "User Data")
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    one_line = (
        f'"{chrome}" --remote-debugging-port=9222 '
        f'--user-data-dir="{user_data}" --profile-directory=Default'
    )
    return (
        "1) Закрой ВСЕ окна Chrome.\n"
        "2) Одна строка в cmd / PowerShell:\n\n"
        f"   {one_line}\n\n"
        "3) Открой TikTok, залогинься, запусти скрипт.\n\n"
        f"Или задай TIKTOK_CDP_URL если порт не 9222 (сейчас: {CDP_URL})."
    )


def _log(msg: str) -> None:
    print(f"[browser] {msg}")


async def _apply_mute_to_context(context: BrowserContext) -> None:
    """Мут не должен ломать запуск браузера — любая ошибка глотается."""
    try:
        try:
            await context.add_init_script(_MUTE_INIT_SCRIPT)
            _log("мут: init_script ок")
        except Exception as e:
            _log(f"мут: init_script пропущен ({e})")
        for page in context.pages:
            try:
                await page.evaluate(_MUTE_INIT_SCRIPT)
            except Exception:
                pass
    except Exception as e:
        _log(f"мут: отключён ({e})")


async def _list_pages(browser: Browser) -> list[tuple[str, Page]]:
    rows: list[tuple[str, Page]] = []
    for ci, ctx in enumerate(browser.contexts):
        for pi, page in enumerate(ctx.pages):
            url = page.url or ""
            rows.append((f"context[{ci}] page[{pi}]: {url}", page))
    return rows


async def _pick_tiktok_page(browser: Browser) -> Page | None:
    for ctx in browser.contexts:
        for page in ctx.pages:
            if "tiktok.com" in (page.url or "").lower():
                return page
    return None


async def _reuse_blank_or_new(browser: Browser) -> Page:
    disposable_prefixes = ("about:", "chrome://newtab")
    for ctx in browser.contexts:
        for page in ctx.pages:
            u = (page.url or "").lower()
            if u == "about:blank" or u.startswith(disposable_prefixes):
                _log(f"reuse tab url={page.url!r}")
                return page
    if not browser.contexts:
        raise RuntimeError("Нет browser context (CDP).")
    _log("new_page()")
    return await browser.contexts[0].new_page()


async def _goto_tiktok(page: Page) -> None:
    _log(f"goto {TIKTOK_URL}")
    await page.goto(TIKTOK_URL, wait_until="domcontentloaded", timeout=90_000)
    _log(f"url now: {page.url!r}")


async def _connect_cdp(pw: Playwright) -> tuple[Browser, Page]:
    _log(f"CDP: connect_over_cdp({CDP_URL}) ...")
    browser = await asyncio.wait_for(
        pw.chromium.connect_over_cdp(CDP_URL),
        timeout=CONNECT_TIMEOUT_S,
    )
    _log("CDP: connected")

    for line, _p in await _list_pages(browser):
        _log(line)

    # Применяем мут ко всем контекстам
    for ctx in browser.contexts:
        await _apply_mute_to_context(ctx)

    page = await _pick_tiktok_page(browser)
    if page is None:
        _log("нет вкладки TikTok — открываю")
        page = await _reuse_blank_or_new(browser)
        await _goto_tiktok(page)
    else:
        _log(f"TikTok уже открыт: {page.url!r}")
        # Применяем мут на текущую страницу сразу
        try:
            await page.evaluate(_MUTE_INIT_SCRIPT)
        except Exception:
            pass

    try:
        await page.bring_to_front()
    except Exception as e:
        _log(f"bring_to_front: {e}")

    return browser, page


async def _launch_persistent_chrome(pw: Playwright) -> tuple[BrowserContext, Page]:
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"CDP нет на {CDP_URL} — запускаю Chrome, профиль: {_PROFILE_DIR}")
    _log("(первый раз залогинься в TikTok в этом окне; куки останутся в папке)")

    _common = dict(
        headless=False,
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"],
    )
    # Первый аргумент — user_data_dir (так надёжнее, чем только **kwargs)
    try:
        context = await pw.chromium.launch_persistent_context(
            str(_PROFILE_DIR),
            channel="chrome",
            **_common,
        )
        _log("запущен системный Google Chrome")
    except Exception as e:
        _log(f"channel=chrome не вышел ({e!r}), пробую Chromium из Playwright")
        context = await pw.chromium.launch_persistent_context(str(_PROFILE_DIR), **_common)

    # Применяем глобальный мут к контексту
    await _apply_mute_to_context(context)

    if context.pages:
        page = context.pages[0]
        _log(f"первая вкладка: {page.url!r}")
    else:
        page = await context.new_page()
        _log("создана новая вкладка")

    if "tiktok.com" not in (page.url or "").lower():
        await _goto_tiktok(page)

    # Мутим текущую страницу сразу
    try:
        await page.evaluate(_MUTE_INIT_SCRIPT)
    except Exception:
        pass

    try:
        await page.bring_to_front()
    except Exception as e:
        _log(f"bring_to_front: {e}")

    return context, page


async def open_tiktok() -> tuple[Browser | BrowserContext, Page, Playwright]:
    """
    Возвращает (handle, page, playwright).
    handle: Browser (CDP) или BrowserContext (persistent) — у обоих есть await close().
    """
    _log(f"проверка CDP: {CDP_URL}/json/version ...")
    use_cdp = _cdp_json_reachable()
    if use_cdp:
        _log("CDP отвечает — режим подключения к уже открытому Chrome")
    else:
        _log("CDP не отвечает — режим автозапуска Chrome (отдельный профиль в проекте)")

    pw = await async_playwright().start()

    if use_cdp:
        try:
            browser, page = await _connect_cdp(pw)
        except asyncio.TimeoutError:
            await pw.stop()
            raise RuntimeError(
                f"Таймаут CDP ({CONNECT_TIMEOUT_S} с). "
                + chrome_launch_instructions()
            ) from None
        except Exception as e:
            await pw.stop()
            raise RuntimeError(
                f"Ошибка CDP: {e}\n\n" + chrome_launch_instructions()
            ) from e
        _log("готово: Enter в терминале после логина")
        return browser, page, pw

    try:
        context, page = await _launch_persistent_chrome(pw)
        _log("готово: залогинься при необходимости, потом Enter в терминале")
        return context, page, pw
    except Exception:
        try:
            await pw.stop()
        except Exception:
            pass
        raise
