"""
Утилиты взаимодействия с браузером (Playwright).
Нет зависимостей от pipeline и scoring — только scroll_config.
"""

import asyncio
import random

from src.scroll_config import FEED_URL


# ── Mute ──────────────────────────────────────────────────────────────────────

async def _ensure_muted(page) -> None:
    """
    Мутит все текущие video/audio.
    Вешает MutationObserver — автоматически мутит новые элементы при добавлении в DOM.
    Observer вешается один раз (guard window.__muteObserver).
    """
    try:
        await page.evaluate("""
            () => {
                document.querySelectorAll('video, audio').forEach(v => {
                    v.muted = true;
                    v.volume = 0;
                });

                if (window.__muteObserver) return;

                window.__muteObserver = new MutationObserver(mutations => {
                    for (const m of mutations) {
                        for (const node of m.addedNodes) {
                            if (!node || !node.tagName) continue;
                            if (node.tagName === 'VIDEO' || node.tagName === 'AUDIO') {
                                node.muted = true;
                                node.volume = 0;
                            }
                            if (node.querySelectorAll) {
                                node.querySelectorAll('video, audio').forEach(v => {
                                    v.muted = true;
                                    v.volume = 0;
                                });
                            }
                        }
                    }
                });
                window.__muteObserver.observe(document.body, {
                    childList: true,
                    subtree: true
                });
            }
        """)
    except Exception as e:
        print(f"Ошибка _ensure_muted: {e}")


# ── Username ──────────────────────────────────────────────────────────────────

async def get_username(page) -> str | None:
    print("Получаю username...")
    try:
        url = page.url
        if "/@" in url and "/video/" in url:
            username = url.split("/@")[1].split("/video/")[0]
            if username and len(username) > 1:
                print(f"USERNAME (url): {username}")
                return username
    except Exception as e:
        print(f"Ошибка get_username url: {e}")

    try:
        username = await page.evaluate("""
            () => {
                const activeSelectors = [
                    '[class*="swiper-slide-active"] a[href*="/@"]',
                    '[class*="ActiveSlide"] a[href*="/@"]',
                    '[class*="active"] a[href*="/@"]',
                ];
                for (const sel of activeSelectors) {
                    const a = document.querySelector(sel);
                    if (a) {
                        const href = a.getAttribute('href') || '';
                        const match = href.match(/\\/@([^/?#]+)/);
                        if (match && match[1] && match[1].length > 1) return match[1];
                    }
                }
                const videos = Array.from(document.querySelectorAll('video'));
                for (const v of videos) {
                    if (!v.paused && !v.ended) {
                        let el = v.parentElement;
                        for (let i = 0; i < 12; i++) {
                            if (!el) break;
                            const a = el.querySelector('a[href*="/@"]');
                            if (a) {
                                const href = a.getAttribute('href') || '';
                                const match = href.match(/\\/@([^/?#]+)/);
                                if (match && match[1] && match[1].length > 1) return match[1];
                            }
                            el = el.parentElement;
                        }
                    }
                }
                return null;
            }
        """)
        if username:
            print(f"USERNAME (dom): {username}")
            return username
    except Exception as e:
        print(f"Ошибка get_username dom: {e}")

    print("username не найден → повтор через 1 сек")
    await asyncio.sleep(1)

    try:
        url = page.url
        if "/@" in url and "/video/" in url:
            username = url.split("/@")[1].split("/video/")[0]
            if username and len(username) > 1:
                print(f"USERNAME (url retry): {username}")
                return username
    except Exception:
        pass

    try:
        username = await page.evaluate("""
            () => {
                const videos = Array.from(document.querySelectorAll('video'));
                for (const v of videos) {
                    if (!v.paused && !v.ended) {
                        let el = v.parentElement;
                        for (let i = 0; i < 12; i++) {
                            if (!el) break;
                            const a = el.querySelector('a[href*="/@"]');
                            if (a) {
                                const href = a.getAttribute('href') || '';
                                const match = href.match(/\\/@([^/?#]+)/);
                                if (match && match[1] && match[1].length > 1) return match[1];
                            }
                            el = el.parentElement;
                        }
                    }
                }
                return null;
            }
        """)
        if username:
            print(f"USERNAME (dom retry): {username}")
            return username
    except Exception as e:
        print(f"Ошибка get_username dom retry: {e}")

    print("username не найден")
    return None


# ── Video ID для дедупликации ─────────────────────────────────────────────────

async def get_video_id(page) -> str | None:
    """
    Возвращает уникальный ключ текущего видео — первые 80 символов src играющего <video>.
    src меняется при каждом переключении видео → надёжный уникальный ключ.
    """
    try:
        video_key = await page.evaluate("""
            () => {
                const videos = Array.from(document.querySelectorAll('video'));
                for (const v of videos) {
                    if (!v.paused && !v.ended && v.src && v.src.length > 10) {
                        return v.src.substring(0, 80);
                    }
                }
                for (const v of videos) {
                    if (v.src && v.src.length > 10) {
                        return v.src.substring(0, 80);
                    }
                }
                return null;
            }
        """)
        return video_key
    except Exception as e:
        print(f"Ошибка get_video_id: {e}")
        return None


# ── Навигация ─────────────────────────────────────────────────────────────────

async def next_video(page, current_username: str | None) -> bool:
    for attempt in range(5):
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(1.5 + attempt * 0.3)
        await _ensure_muted(page)
        new_username = await get_username(page)
        if new_username and new_username != current_username:
            print(f"Переключено через ArrowDown (попытка {attempt + 1})")
            return True
        print(f"ArrowDown {attempt + 1}/5 — username не сменился")

    print("ArrowDown не помог → пробую mouse.wheel")
    for attempt in range(3):
        await page.mouse.wheel(0, 1000)
        await asyncio.sleep(2)
        await _ensure_muted(page)
        new_username = await get_username(page)
        if new_username and new_username != current_username:
            print(f"Переключено через mouse.wheel (попытка {attempt + 1})")
            return True
        print(f"mouse.wheel {attempt + 1}/3 — username не сменился")

    print("Не удалось переключить видео")
    return False


_PLAYING_IS_PLAYING_JS = """
() => {
    const videos = Array.from(document.querySelectorAll('video'));
    let playing = null;
    for (const v of videos) {
        if (!v.paused && !v.ended && v.src && v.src.length > 8) {
            playing = v;
            break;
        }
    }
    if (!playing) {
        for (const v of videos) {
            if (v.src && v.src.length > 8) { playing = v; break; }
        }
    }
    if (!playing && videos.length) playing = videos[0];
    return playing ? !playing.paused : false;
}
"""

_FOCUS_PLAYING_VIDEO_JS = """
() => {
    const videos = Array.from(document.querySelectorAll('video'));
    let playing = null;
    for (const v of videos) {
        if (!v.paused && !v.ended && v.src && v.src.length > 8) {
            playing = v;
            break;
        }
    }
    if (!playing) {
        for (const v of videos) {
            if (v.src && v.src.length > 8) { playing = v; break; }
        }
    }
    if (!playing && videos.length) playing = videos[0];
    if (playing) playing.focus();
}
"""


async def ensure_video_playing(page) -> None:
    try:
        await page.bring_to_front()
        await asyncio.sleep(0.1)

        is_playing = await page.evaluate(_PLAYING_IS_PLAYING_JS)

        if is_playing:
            print("Видео играет")
            await _ensure_muted(page)
            return

        print("Видео не играет → пробую Space")
        await asyncio.sleep(0.2)
        await page.keyboard.press("Space", delay=random.randint(50, 150))
        await asyncio.sleep(0.5)

        is_playing = await page.evaluate(_PLAYING_IS_PLAYING_JS)

        if not is_playing:
            print("Space не сработал → фокус + Space")
            await page.evaluate(_FOCUS_PLAYING_VIDEO_JS)
            await asyncio.sleep(0.15)
            await page.keyboard.press("Space", delay=random.randint(50, 150))
            await asyncio.sleep(0.5)

    except Exception as e:
        print(f"Ошибка ensure_video_playing: {e}")

    await _ensure_muted(page)


async def return_to_feed(page) -> None:
    """
    Надёжный возврат в ленту — всегда явный goto, без go_back.
    go_back() ненадёжен: после DM и похожих история браузера непредсказуема.
    """
    print("Возвращаюсь в ленту")
    for attempt in range(3):
        try:
            await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(2)
            has_video = await asyncio.wait_for(
                page.evaluate("() => !!document.querySelector('video')"),
                timeout=3
            )
            if has_video:
                await _ensure_muted(page)
                await ensure_video_playing(page)
                print("Возврат в ленту ✓")
                return
            print(f"Нет видео после goto (попытка {attempt + 1}) → повтор")
        except Exception as e:
            print(f"Ошибка возврата (попытка {attempt + 1}): {e}")
        await asyncio.sleep(3)
    print("Не удалось вернуться в ленту после 3 попыток")


# ── Видео ─────────────────────────────────────────────────────────────────────

async def _get_video_duration(page) -> float:
    """Длительность того же «playing» ролика, что и в scroll snapshot (не первый video в DOM)."""
    try:
        duration = await page.evaluate(
            """() => {
                const videos = Array.from(document.querySelectorAll('video'));
                let playing = null;
                for (const v of videos) {
                    if (!v.paused && !v.ended && v.src && v.src.length > 8) {
                        playing = v;
                        break;
                    }
                }
                if (!playing) {
                    for (const v of videos) {
                        if (v.src && v.src.length > 8) { playing = v; break; }
                    }
                }
                if (!playing && videos.length) playing = videos[0];
                return playing && playing.duration > 0 ? playing.duration : 0;
            }"""
        )
        if duration and duration > 0 and duration < 300:
            return float(duration)
    except Exception:
        pass
    return 10.0


async def _watch_video(page, seconds: float) -> None:
    print(f"Смотрю видео {seconds:.1f}с")
    await asyncio.sleep(seconds)


# ── Действия ──────────────────────────────────────────────────────────────────

async def _do_like(page) -> None:
    """Лайкает текущее видео в ленте."""
    _LIKE_SELECTORS = [
        '[data-e2e="like-icon"]',
        '[data-e2e="browse-like-icon"]',
        'button[aria-label*="ike"]',
        'span[data-e2e="like-icon"]',
    ]
    for sel in _LIKE_SELECTORS:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=2000)
            await el.click()
            print("Лайк поставлен ✓")
            return
        except Exception:
            continue
    print("Кнопка лайка не найдена")


async def _do_follow(page, username: str) -> None:
    """Подписка на аккаунт со страницы профиля."""
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1)

        _FOLLOW_SELECTORS = [
            '[data-e2e="user-follow-button"]',
            '[data-e2e="follow-button"]',
            'button:has-text("Follow")',
            'button:has-text("Підписатися")',
            'button:has-text("Подписаться")',
            'button[class*="follow" i]',
        ]
        _ALREADY = ["Following", "Підписаний", "Подписан", "Friends"]

        follow_btn = None
        for sel in _FOLLOW_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=3000)
                follow_btn = btn
                print(f"Follow кнопка найдена ({sel})")
                break
            except Exception:
                continue

        if follow_btn is None:
            print(f"Кнопка Follow не найдена для @{username}")
            return

        btn_text = await follow_btn.inner_text()
        if any(w.lower() in btn_text.lower() for w in _ALREADY):
            print(f"Уже подписан на @{username}")
            return

        await follow_btn.click()
        await asyncio.sleep(1.5)
        new_text = await follow_btn.inner_text()
        print(f"Подписался на @{username} ✓ (кнопка: {new_text!r})")

    except Exception as e:
        print(f"Ошибка подписки @{username}: {e}")


# ── AI хелпер (синхронный, для asyncio.to_thread) ────────────────────────────

def _check_frame_has_cyrillic_text(screenshot_bytes: bytes) -> bool:
    """
    Проверяет наличие кириллицы на кадре через llava.
    Вызывается через asyncio.to_thread — импорты внутри намеренно.
    True → есть кириллица → продолжаем.
    False → нет кириллицы / ошибка → скип.
    """
    import base64
    import ollama
    import time

    image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    prompt = (
        "Look at this TikTok video frame.\n"
        "Is there any Cyrillic text (Russian or Ukrainian letters) visible on screen?\n\n"
        "- Say YES if you can see Cyrillic text (captions, subtitles, overlays, signs)\n"
        "- Say NO if there is only English text, no text at all, or you are not sure\n\n"
        "Reply ONE word only: YES or NO."
    )
    for attempt in range(2):
        try:
            response = ollama.chat(
                model="llava:7b",
                messages=[{"role": "user", "content": prompt, "images": [image_b64]}],
                options={"num_predict": 5},
            )
            raw = response["message"]["content"].strip().upper()
            print(f"Кадр кириллица llava: {raw!r}")
            return raw.startswith("YES")
        except Exception as e:
            print(f"Ошибка llava кириллица (попытка {attempt + 1}): {e}")
            time.sleep(2)
    return False
