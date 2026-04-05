"""
Основной цикл ленты For You.

Модель: 1 видео = 1 полный цикл без скролла ленты до завершения.

State machine (анти–race с асинхронным UI TikTok):
  1) Ожидание смены src относительно last_completed_src (после скролла).
  2) Стабилизация: src не меняется заданное время (FEED_SRC_STABLE_MS_* в scroll_config).
  3) Snapshot A (полный VideoContext в одном evaluate).
  4) Пауза FEED_SNAPSHOT_VERIFY_GAP_MS_* → snapshot B; если A≠B — abort, скролл дальше.
  5) Только после совпадения A/B — фильтры, лайк, просмотр, analyze.

- _feed_processing: пока True — запрещены ArrowDown / wheel по ленте (скролл только после снятия флага).
- VideoContext после этапов 3–4 единственный источник для пайплайна.
- Проверка смены ролика во время analyze — video_src (+ username) в stale guard.

JS scroll-lock: только после лайка; снятие в finally и перед любым программным скроллом ленты.

Переключение ролика: ArrowDown → wheel → …; после шага — ожидание смены video_src; залипание → reload.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

from playwright.async_api import Page

from src.scroll_config import (
    FEED_SNAPSHOT_VERIFY_GAP_MS_MAX,
    FEED_SNAPSHOT_VERIFY_GAP_MS_MIN,
    FEED_SRC_STABLE_MAX_WAIT_MS,
    FEED_SRC_STABLE_MS_MAX,
    FEED_SRC_STABLE_MS_MIN,
    FEED_URL,
    FEED_WAIT_PREVIOUS_SRC_CHANGE_MS,
    HARD_STOPWORDS,
    _JS_LOCK_FEED_SCROLL,
    _JS_UNLOCK_SCROLL,
)
from src.scoring import has_relevant, is_cis, is_clearly_irrelevant
from src.page_utils import (
    _do_like,
    _ensure_muted,
    _get_video_duration,
    _watch_video,
    ensure_video_playing,
)
from src.models import VideoContext
from src.profile import analyze_one_video
from src.database import is_checked

# Ожидание смены video_src после одного действия (не 18с × N — иначе «зависание»)
_CHANGE_WAIT_SHORT_MS = 8_000
_STALE_POLL_S = 0.55
# Сколько полных проходов (ArrowDown → wheel → click+Arrow → scrollBy) до reload
_MAX_FALLBACK_ROUNDS = 2

# Пока True — нельзя листать ленту (ArrowDown / wheel для смены ролика)
_feed_processing: bool = False

_SNAPSHOT_JS = """
() => {
    function pickPlaying() {
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
        return playing;
    }

    /**
     * Подпись только из той же «ячейки», что и playing: не document.querySelector,
     * иначе в ленте берётся desc соседнего слайда (первый в DOM).
     */
    function captionForPlayingVideo(playing) {
        if (!playing) return { caption: "", caption_scoped: false };
        let el = playing.parentElement;
        for (let i = 0; i < 22; i++) {
            if (!el) break;
            const vids = el.querySelectorAll('video');
            if (vids.length === 1 && vids[0] === playing) {
                const desc = el.querySelector('[data-e2e="video-desc"]');
                if (desc) {
                    return {
                        caption: (desc.innerText || "").trim(),
                        caption_scoped: true,
                    };
                }
            }
            el = el.parentElement;
        }
        el = playing.parentElement;
        for (let i = 0; i < 22; i++) {
            if (!el) break;
            const descs = el.querySelectorAll('[data-e2e="video-desc"]');
            if (descs.length === 1) {
                return {
                    caption: (descs[0].innerText || "").trim(),
                    caption_scoped: true,
                };
            }
            el = el.parentElement;
        }
        const g = document.querySelector('[data-e2e="video-desc"]');
        const h1 = document.querySelector('h1');
        const fallback = g
            ? (g.innerText || "")
            : (h1 ? h1.innerText : "");
        return { caption: (fallback || "").trim(), caption_scoped: false };
    }

    const playing = pickPlaying();
    if (!playing) {
        return {
            username: null,
            caption: "",
            video_url: location.href,
            video_src: null,
            caption_scoped: false,
        };
    }
    const video_src = playing.src ? playing.src.substring(0, 120) : null;
    let el = playing.parentElement;
    let username = null;
    for (let i = 0; i < 18; i++) {
        if (!el) break;
        const links = el.querySelectorAll('a[href*="/@"]');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            const m = href.match(/@([^/?#]+)/);
            if (m && m[1].length > 1 && m[1] !== 'tiktok') {
                username = m[1];
                break;
            }
        }
        if (username) break;
        el = el.parentElement;
    }
    const cap = captionForPlayingVideo(playing);
    return {
        username: username,
        caption: cap.caption,
        video_url: location.href,
        video_src: video_src,
        caption_scoped: cap.caption_scoped,
    };
}
"""

# Только идентичность ролика (без caption) — для stale guard и сравнения после скролла
# Логика pickPlaying идентична _SNAPSHOT_JS (один «текущий» ролик).
_IDENTITY_JS = """
() => {
    function pickPlaying() {
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
        return playing;
    }
    const playing = pickPlaying();
    if (!playing) return { username: null, video_src: null };
    const video_src = playing.src ? playing.src.substring(0, 120) : null;
    let el = playing.parentElement;
    let username = null;
    for (let i = 0; i < 18; i++) {
        if (!el) break;
        const links = el.querySelectorAll('a[href*="/@"]');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            const m = href.match(/@([^/?#]+)/);
            if (m && m[1].length > 1 && m[1] !== 'tiktok') {
                username = m[1];
                break;
            }
        }
        if (username) break;
        el = el.parentElement;
    }
    return { username: username, video_src: video_src };
}
"""


def _log(msg: str) -> None:
    print(f"[scroll] {msg}")


async def _ensure_feed_scroll_unlocked(page: Page) -> None:
    """
    Гарантированное снятие JS-блокировки скролла (идемпотентно).
    Вызывать перед любым программным скроллом ленты и в finally после lock.
    """
    try:
        await page.evaluate(_JS_UNLOCK_SCROLL)
    except Exception as e:
        _log(f"unlock scroll (игнор): {e}")


async def _lock_feed_scroll(page: Page) -> None:
    """Блокировка только после фиксации snap и прохождения фильтров (вызывать после лайка)."""
    await page.evaluate(_JS_LOCK_FEED_SCROLL)


def _begin_feed_video(snap: VideoContext) -> None:
    global _feed_processing
    if _feed_processing:
        raise RuntimeError("вложенная обработка: _begin_feed_video при активном флаге")
    _feed_processing = True
    _log(f"processing_video=True → @{snap.username}")


def _end_feed_video_safe() -> None:
    global _feed_processing
    if _feed_processing:
        _feed_processing = False
        _log("processing_video=False")


async def _take_snapshot(page: Page) -> VideoContext | None:
    """Полный снимок для фиксации ролика (единственное чтение caption для цикла)."""
    try:
        r = await page.evaluate(_SNAPSHOT_JS)
        if not r:
            return None
        u = r.get("username")
        if not u:
            return None
        if r.get("caption_scoped") is False and (r.get("caption") or "").strip():
            _log(
                "caption взят fallback (не привязан к ячейке playing) — "
                "возможна рассинхронизация; проверьте DOM TikTok"
            )
        return VideoContext(
            username=u,
            caption=r.get("caption") or "",
            video_url=r.get("video_url") or "",
            video_src=(r.get("video_src") or "")[:120],
        )
    except Exception as e:
        _log(f"snapshot error: {e}")
        return None


async def _read_identity_only(page: Page) -> tuple[str | None, str]:
    """Только username + video_src — не caption (для stale и смены ролика)."""
    try:
        r = await page.evaluate(_IDENTITY_JS)
        if not r:
            return None, ""
        u = r.get("username")
        src = (r.get("video_src") or "")[:120]
        return (u, src)
    except Exception as e:
        _log(f"identity error: {e}")
        return None, ""


def _identity_matches_snap(
    username: str | None, video_src: str, snap: VideoContext
) -> bool:
    if snap.video_src and video_src and snap.video_src == video_src:
        return True
    if snap.username and username and snap.username == username:
        return True
    return False


def _video_contexts_equal(a: VideoContext, b: VideoContext) -> bool:
    """Двойная проверка: один и тот же ролик (src + автор + подпись + url)."""
    return (
        (a.video_src or "") == (b.video_src or "")
        and (a.username or "") == (b.username or "")
        and (a.caption or "").strip() == (b.caption or "").strip()
        and (a.video_url or "") == (b.video_url or "")
    )


async def _wait_until_playing_src_differs_from_completed(
    page: Page,
    last_completed_src: str | None,
) -> None:
    """
    Этап 1: после скролла UI может кратко показывать старый src.
    Ждём, пока текущий playing src отличается от завершённого цикла (или таймаут).
    """
    if not last_completed_src or last_completed_src in ("", "__empty__"):
        return
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < FEED_WAIT_PREVIOUS_SRC_CHANGE_MS:
        _, src = await _read_identity_only(page)
        if src and src != last_completed_src:
            _log(
                f"этап1: src отличается от прошлого цикла "
                f"…{last_completed_src[:40]} → …{src[:40]}"
            )
            return
        await asyncio.sleep(0.1)
    _log(
        f"этап1: за {FEED_WAIT_PREVIOUS_SRC_CHANGE_MS}ms src всё ещё как у "
        f"прошлого цикла — возможен тот же ролик или лаг DOM"
    )


async def _wait_for_playing_src_stable(
    page: Page,
    *,
    stable_for_ms: int,
    poll_s: float = 0.08,
    max_wait_s: float | None = None,
) -> tuple[str | None, str]:
    """
    Этап 2: src не должен «мелькать» — ждём, пока префикс нестабилен меньше stable_for_ms.
    Любая смена src сбрасывает отсчёт.
    """
    max_w = (FEED_SRC_STABLE_MAX_WAIT_MS / 1000.0) if max_wait_s is None else max_wait_s
    last_src: str | None = None
    stable_since: float | None = None
    deadline = time.monotonic() + max_w
    while time.monotonic() < deadline:
        u, src = await _read_identity_only(page)
        if not src:
            last_src = None
            stable_since = None
        elif src != last_src:
            last_src = src
            stable_since = time.monotonic()
            _log(f"этап2: src изменился → сброс стабилизации …{src[:56]}…")
        else:
            if last_src and stable_since is not None:
                if (time.monotonic() - stable_since) * 1000 >= stable_for_ms:
                    _log(f"этап2: src стабилен ≥{stable_for_ms}ms …{last_src[:56]}…")
                    return u, last_src
        await asyncio.sleep(poll_s)
    _log("этап2: таймаут стабилизации src — используем последнее чтение")
    u, src = await _read_identity_only(page)
    return u, src or ""


async def _take_snapshot_verified_pair(
    page: Page,
    *,
    verify_gap_s: float,
) -> tuple[VideoContext | None, VideoContext | None, str]:
    """
    Этапы 3–4: два полных снимка с паузой. Возвращает (snap_a, snap_b, reason).
    reason: '' если совпали; иначе 'verify_mismatch' | 'empty' | 'no_user'.
    """
    a = await _take_snapshot(page)
    if not a or not a.username:
        return None, None, "no_user"
    _log(f"snapshot A: src={a.video_src[:48] if a.video_src else '∅'}… @{a.username}")
    await asyncio.sleep(verify_gap_s)
    b = await _take_snapshot(page)
    if not b or not b.username:
        return a, None, "empty"
    _log(f"snapshot B: src={b.video_src[:48] if b.video_src else '∅'}… @{b.username}")
    if not _video_contexts_equal(a, b):
        _log(
            "ABORT drift: snapshot A ≠ B | "
            f"src {a.video_src[:40]!r} vs {b.video_src[:40]!r} | "
            f"user {a.username!r} vs {b.username!r} | "
            f"caption_eq={(a.caption or '').strip() == (b.caption or '').strip()}"
        )
        return a, b, "verify_mismatch"
    _log("этап4: двойная проверка ✓ (username, caption, src, url совпадают)")
    return a, b, ""


async def _baseline_for_scroll(page: Page, snap: VideoContext) -> VideoContext:
    """
    Эталон для ожидания смены ролика: то, что сейчас в DOM.
    Если snap устарел (drift), сравнение должно идти с актуальным src/username,
    иначе wait_for_function может сработать некорректно.
    """
    u, src = await _read_identity_only(page)
    if snap.video_src and src and snap.video_src == src:
        return snap
    if u or src:
        return VideoContext(
            username=u or snap.username or "",
            caption=snap.caption,
            video_url=page.url or snap.video_url or "",
            video_src=(src or "")[:120],
        )
    return snap


async def _wait_dom_ready(page: Page) -> None:
    """Готовность текущего playing-видео. Не ждём глобальный video-desc — он мог быть от другого слайда."""
    try:
        await page.wait_for_selector("video", state="attached", timeout=12_000)
    except Exception as e:
        _log(f"wait video attached: {e}")
    try:
        await page.wait_for_function(
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
                return playing && playing.readyState >= 2;
            }""",
            timeout=10_000,
        )
    except Exception as e:
        _log(f"wait playing video readyState: {e}")


async def _wait_until_snapshot_differs(
    page: Page, before: VideoContext, timeout_ms: int = _CHANGE_WAIT_SHORT_MS
) -> bool:
    try:
        await page.wait_for_function(
            """(b) => {
                const videos = Array.from(document.querySelectorAll('video'));
                let playing = null;
                for (const v of videos) {
                    if (!v.paused && !v.ended && v.src && v.src.length > 8) {
                        playing = v; break;
                    }
                }
                if (!playing && videos.length) {
                    for (const v of videos) {
                        if (v.src && v.src.length > 8) { playing = v; break; }
                    }
                }
                if (!playing && videos.length) playing = videos[0];
                if (!playing) return false;
                const src = playing.src ? playing.src.substring(0, 120) : '';
                if (b.video_src && src && src !== b.video_src) return true;
                let el = playing.parentElement;
                let uname = null;
                for (let i = 0; i < 18; i++) {
                    if (!el) break;
                    const links = el.querySelectorAll('a[href*="/@"]');
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/@([^/?#]+)/);
                        if (m && m[1].length > 1 && m[1] !== 'tiktok') {
                            uname = m[1];
                            break;
                        }
                    }
                    if (uname) break;
                    el = el.parentElement;
                }
                if (uname && b.username && uname !== b.username) return true;
                return false;
            }""",
            arg={
                "video_src": before.video_src or "",
                "username": before.username or "",
            },
            timeout=timeout_ms,
        )
        return True
    except Exception as e:
        _log(f"нет смены ролика за {timeout_ms}ms: {e}")
        return False


async def _focus_video_for_scroll(page: Page) -> None:
    """Фокус окна + клик по центру видео (TikTok часто игнорирует клавиши без фокуса)."""
    await page.bring_to_front()
    try:
        v = page.locator("video").first
        await v.wait_for(state="visible", timeout=4_000)
        box = await v.bounding_box()
        if box and box.get("width", 0) > 0:
            await page.mouse.click(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
    except Exception as e:
        _log(f"focus video: {e}")
    await asyncio.sleep(0.12)


async def _method_arrow_down(page: Page) -> None:
    await page.keyboard.press("ArrowDown")


async def _method_wheel(page: Page) -> None:
    await page.mouse.wheel(0, random.randint(900, 1600))


async def _method_click_then_arrow(page: Page) -> None:
    await _focus_video_for_scroll(page)
    await page.keyboard.press("ArrowDown")


async def _method_window_scroll_by(page: Page) -> None:
    await page.evaluate(
        "() => { window.scrollBy(0, Math.min(600, window.innerHeight * 0.35)); }"
    )


async def _reload_and_return_to_feed(page: Page) -> None:
    """Последний fallback: перезагрузка и явный заход в ленту For You."""
    _log("reload страницы…")
    try:
        await page.reload(wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        _log(f"reload: {e}")
    try:
        await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception as e:
        _log(f"goto For You: {e}")
    await _ensure_muted(page)
    try:
        await ensure_video_playing(page)
        await _wait_dom_ready(page)
    except Exception as e:
        _log(f"после reload: {e}")


async def _keyboard_scroll_and_wait_change(
    page: Page, before: VideoContext, reason: str = ""
) -> None:
    """
    Надёжное переключение ролика: цепочка действий + короткое ожидание смены video_src.
    Если несколько раундов не дали смены — reload + лента (гарантированный выход из залипания).
    """
    label = reason or "next"
    _log(f"advance_feed начало ({label}) — эталон src={before.video_src[:48] if before.video_src else '∅'}…")

    steps: list[tuple[str, Callable[[Page], Awaitable[None]]]] = [
        ("ArrowDown", _method_arrow_down),
        ("mouse.wheel", _method_wheel),
        ("click+ArrowDown", _method_click_then_arrow),
        ("window.scrollBy", _method_window_scroll_by),
    ]

    for round_i in range(_MAX_FALLBACK_ROUNDS):
        _log(f"раунд fallback {round_i + 1}/{_MAX_FALLBACK_ROUNDS}")
        for step_name, fn in steps:
            await _focus_video_for_scroll(page)
            await fn(page)
            if await _wait_until_snapshot_differs(
                page, before, timeout_ms=_CHANGE_WAIT_SHORT_MS
            ):
                _log(f"ролик сменился после «{step_name}» ✓")
                await _ensure_muted(page)
                return
            _log(f"нет смены video_src после «{step_name}»")

    _log("все шаги исчерпаны — залипание → reload + For You")
    await _reload_and_return_to_feed(page)
    await _ensure_muted(page)


async def _scroll_feed_next(
    page: Page, before: VideoContext, reason: str
) -> None:
    """Скролл ленты только когда processing_video=False."""
    global _feed_processing
    if _feed_processing:
        raise RuntimeError(
            f"SCROLL запрещён: processing_video=True (reason={reason})"
        )
    await _ensure_feed_scroll_unlocked(page)
    await _keyboard_scroll_and_wait_change(page, before, reason)


async def _end_processing_and_scroll_feed(
    page: Page, before: VideoContext, reason: str
) -> None:
    """Завершить цикл одного видео и перейти к следующему ролику."""
    _end_feed_video_safe()
    await _ensure_feed_scroll_unlocked(page)
    await _keyboard_scroll_and_wait_change(page, before, reason)


async def _analyze_with_stale_guard(page: Page, snap: VideoContext) -> None:
    """
    analyze только с forced_* из snap.
    Смена ролика: сравнение identity (video_src / username), без чтения caption.
    """
    t = asyncio.create_task(
        analyze_one_video(
            page,
            return_url=FEED_URL,
            forced_username=snap.username,
            forced_caption=snap.caption,
        )
    )
    while not t.done():
        await asyncio.sleep(_STALE_POLL_S)
        u, src = await _read_identity_only(page)
        if snap.video_src and src and src != snap.video_src:
            _log("video_src изменился во время analyze → отмена + unlock")
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            await _ensure_feed_scroll_unlocked(page)
            return
        if snap.username and u and u != snap.username:
            _log("username (identity) изменился во время analyze → отмена + unlock")
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            await _ensure_feed_scroll_unlocked(page)
            return
    try:
        await t
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Ошибка analyze_one_video: {e}")


async def human_scroll(page: Page) -> None:
    print("Устанавливаю авто-мут...")
    await _ensure_muted(page)
    print("Начинаю скролл ленты For You (1 ролик = 1 цикл, скролл после обработки)")
    await page.bring_to_front()

    i = 0
    last_dedup_key: str | None = None
    last_completed_src: str | None = None

    while True:
        i += 1
        print(f"\n{'='*40}\nВидео {i}")

        assert not _feed_processing, "цикл: processing должен быть False"

        await _ensure_feed_scroll_unlocked(page)

        await ensure_video_playing(page)
        await _wait_dom_ready(page)

        await _wait_until_playing_src_differs_from_completed(page, last_completed_src)

        stable_ms = random.randint(FEED_SRC_STABLE_MS_MIN, FEED_SRC_STABLE_MS_MAX)
        await _wait_for_playing_src_stable(page, stable_for_ms=stable_ms)

        gap_ms = random.randint(
            FEED_SNAPSHOT_VERIFY_GAP_MS_MIN, FEED_SNAPSHOT_VERIFY_GAP_MS_MAX
        )
        snap_a, _, verify_reason = await _take_snapshot_verified_pair(
            page, verify_gap_s=gap_ms / 1000.0
        )

        if verify_reason == "verify_mismatch":
            assert snap_a is not None
            _log("abort: перехожу к следующему ролику (verify drift)")
            await _scroll_feed_next(page, snap_a, "snapshot_verify_drift")
            continue

        if verify_reason == "empty" and snap_a is not None:
            _log("abort: второй снимок пуст после паузы → скролл")
            await _scroll_feed_next(page, snap_a, "verify_second_empty")
            continue

        if verify_reason == "no_user" or snap_a is None or not snap_a.username:
            _log(f"нет username после стабилизации ({verify_reason}) → скролл")
            await _scroll_feed_next(
                page,
                VideoContext("", "", page.url or "", "__empty__"),
                "no_user",
            )
            continue

        snap = snap_a

        if last_completed_src and snap and snap.video_src == last_completed_src:
            _log("тот же video_src → скролл (idle)")
            fake = VideoContext(
                snap.username, snap.caption, snap.video_url, snap.video_src
            )
            await _scroll_feed_next(page, fake, "unstick")
            continue

        _begin_feed_video(snap)
        try:
            print(f"USERNAME (зафиксировано): {snap.username}")
            cap_preview = snap.caption[:120] + "…" if len(snap.caption) > 120 else snap.caption
            print(f"CAPTION (зафиксировано): {cap_preview!r}")
            _u = snap.video_url or ""
            print(f"VideoContext: @{snap.username} | {_u[:80]} | src={snap.video_src[:40]}…")

            dedup_key = snap.video_src if snap.video_src else f"u:{snap.username}"
            if dedup_key and dedup_key == last_dedup_key:
                print("Дубликат видео → скип")
                last_completed_src = snap.video_src or None
                await _end_processing_and_scroll_feed(page, snap, "dedup")
                continue

            last_dedup_key = dedup_key

            if is_checked(snap.username):
                print(f"@{snap.username} уже проверен → скип")
                last_completed_src = snap.video_src or None
                await _end_processing_and_scroll_feed(page, snap, "db_skip")
                continue

            caption = snap.caption

            if is_clearly_irrelevant(caption):
                print("Нерелевантный контент → скип")
                await _watch_video(page, random.uniform(1, 2))
                last_completed_src = snap.video_src or None
                await _end_processing_and_scroll_feed(page, snap, "filter_irrel")
                continue

            if caption:
                cap_low = caption.lower()
                hit = next((w for w in HARD_STOPWORDS if w in cap_low), None)
                if hit:
                    print(f"Жёсткое стоп-слово '{hit}' → скип")
                    await _watch_video(page, random.uniform(1, 2))
                    last_completed_src = snap.video_src or None
                    await _end_processing_and_scroll_feed(page, snap, "hard_stop")
                    continue

            if is_cis(caption):
                if caption and not has_relevant([caption]):
                    print("Нет релевантных слов в кирилличном caption → скип")
                    await _watch_video(page, random.uniform(1, 2))
                    last_completed_src = snap.video_src or None
                    await _end_processing_and_scroll_feed(page, snap, "cis_skip")
                    continue
            else:
                if caption and not has_relevant([caption]):
                    print("Нет кириллицы и нет релевантных слов → скип (без llava)")
                    await _watch_video(page, random.uniform(1, 2))
                    last_completed_src = snap.video_src or None
                    await _end_processing_and_scroll_feed(page, snap, "no_rel")
                    continue
                print("Нет кириллицы в caption → проверяю кадр")
                try:
                    from src.page_utils import _check_frame_has_cyrillic_text

                    screenshot_lang = await page.screenshot(type="jpeg", quality=60)
                    frame_cyr = await asyncio.to_thread(
                        _check_frame_has_cyrillic_text, screenshot_lang
                    )
                except Exception as e:
                    print(f"Ошибка скриншота: {e} → скип")
                    frame_cyr = False

                if not frame_cyr:
                    print("На кадре нет кириллицы → скип")
                    await _watch_video(page, random.uniform(1, 2))
                    last_completed_src = snap.video_src or None
                    await _end_processing_and_scroll_feed(page, snap, "frame_skip")
                    continue
                print("На кадре кириллица → продолжаем ✓")

            print("Наша тематика ✓ → лайк + блокировка + досматриваю видео")

            await _do_like(page)

            try:
                try:
                    await _lock_feed_scroll(page)
                    print(
                        f"Скролл ленты заблокирован (JS), lock src="
                        f"{snap.video_src[:40] if snap.video_src else '∅'}…"
                    )
                except Exception as e:
                    print(f"Ошибка блокировки: {e} → продолжаем без lock")

                duration = await _get_video_duration(page)
                watch_time = max(3.0, min(duration - 2.0, 30.0))
                print(f"Длина видео: {duration:.1f}с → смотрю {watch_time:.1f}с")
                await _watch_video(page, watch_time)

                try:
                    await page.evaluate(
                        """
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
                            if (playing) { playing.loop = false; playing.pause(); }
                        }
                        """
                    )
                except Exception:
                    pass

                u1, src1 = await _read_identity_only(page)
                if not _identity_matches_snap(u1, src1, snap):
                    print("ролик сдвинулся до analyze → скип analyze")
                    last_completed_src = snap.video_src or None
                    await _end_processing_and_scroll_feed(page, snap, "pre_analyze_drift")
                    continue

                await _analyze_with_stale_guard(page, snap)

            finally:
                await _ensure_feed_scroll_unlocked(page)
                print("JS блокировка скролла снята (finally)")

            current = page.url
            if "tiktok.com/@" in current or (current and "tiktok.com" not in current):
                print("Страница на профиле → возврат в ленту")
                try:
                    await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=15_000)
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    await _ensure_muted(page)
                    await ensure_video_playing(page)
                    await _wait_dom_ready(page)
                except Exception as e:
                    print(f"Ошибка возврата: {e}")

                leave = await _take_snapshot(page)
                for _skip in range(3):
                    if (
                        leave
                        and leave.username
                        and leave.username != snap.username
                    ):
                        print(f"Продвинулись вперёд: @{leave.username}")
                        break
                    print(f"Всё ещё @{snap.username} → ArrowDown #{_skip + 1}")
                    prev_leave = leave or snap
                    await _end_processing_and_scroll_feed(page, prev_leave, "leave_profile")
                    leave = await _take_snapshot(page)

                last_dedup_key = None
                if leave:
                    last_completed_src = leave.video_src or None
                print("после профиля — новая итерация")
                continue

            u_post, src_post = await _read_identity_only(page)
            if not _identity_matches_snap(u_post, src_post, snap):
                print("post-analyze: identity не совпадает со snap → цикл с текущим DOM")
                last_completed_src = src_post or snap.video_src or None
                print("Следующее видео ↓")
                await _end_processing_and_scroll_feed(
                    page,
                    await _baseline_for_scroll(page, snap),
                    "post_analyze_drift",
                )
                continue

            last_completed_src = snap.video_src or None
            print("Следующее видео ↓")
            await _end_processing_and_scroll_feed(
                page,
                await _baseline_for_scroll(page, snap),
                "main_advance",
            )

        finally:
            _end_feed_video_safe()
