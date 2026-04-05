"""
Пайплайн анализа одного TikTok-видео/профиля.

Единственная точка входа: analyze_one_video()
Вызывается из: scroll.py (human_scroll), search_mode.py, similar.py
"""

import asyncio
import datetime

from src.scroll_config import (
    FEED_URL,
    HARD_STOPWORDS,
    MIN_SCORE_FOR_AI,
    SOFT_STOPWORDS,
    TIMEOUT,
)
from src.scoring import (
    has_relevant,
    has_target,
    is_cis,
    is_clearly_irrelevant,
    parse_ai_result,
    parse_views,
    score_avg_views,
    score_bio,
    score_caption,
    score_posting_frequency,
)
from src.page_utils import _check_frame_has_cyrillic_text, _do_follow, get_username
from src.database import is_checked, mark_checked, save_account
from src.ai.analyzer import analyze_account, analyze_frame


def _log(msg: str) -> None:
    print(msg)


async def analyze_one_video(
    page,
    return_url: str = "",
    forced_username: str = "",
    forced_caption: str = "",
    _depth: int = 0,       # ← ограничение рекурсии: similar вызывает с _depth=1
) -> bool:
    """
    Полный анализ одного видео.

    Пайплайн:
      0.  Блеклист (DB)
      1.  СНГ фильтр
      2.  Нерелевантный контент + стоп-слова
      3.  Скоринг caption
      4.  Дата видео по URL
      4a. llava КОНТЕНТ (только если cap_score < 3)
      5.  Переход в профиль
      6.  AVG просмотры + ссылки
      7.  Дата последнего видео
      8.  Частота постинга
      9.  Скоринг bio
      10. Gate MIN_SCORE_FOR_AI
      11. AI анализ (phi3)
      12. Сохранение + подписка + DM + похожие (только если _depth == 0)

    Параметры:
      forced_username — передаётся из human_scroll, надёжнее чем get_username
                        после лайка/просмотра (страница могла листнуться).
      forced_caption  — caption прочитанный до лайка/просмотра. Не перечитываем
                        после sleep, т.к. TikTok мог auto-scroll на следующее видео.
      return_url      — URL для возврата после похожих аккаунтов.
      _depth          — внутренний параметр: 0 = основной вызов, 1 = из similar.
                        При _depth > 0 шаг похожих НЕ запускается (нет рекурсии).
    """
    # ── Username ──────────────────────────────────────────────────────────────
    if forced_username:
        username = forced_username
        _log(f"USERNAME (forced): {username}")
    else:
        username = await get_username(page)
        if not username:
            _log("Не найден username → пропуск")
            return False

    # 0. Блеклист
    if is_checked(username):
        _log(f"@{username} уже проверен ранее → пропуск")
        return False

    # ── Caption ───────────────────────────────────────────────────────────────
    # forced_caption передаётся из human_scroll — не перечитываем после sleep,
    # т.к. TikTok мог auto-scroll и DOM показывает caption следующего видео.
    if forced_caption:
        caption = forced_caption
        _log(f"CAPTION (forced): {caption}")
    else:
        try:
            caption = await asyncio.wait_for(
                page.evaluate("""
                    () => {
                        const el = document.querySelector('[data-e2e="video-desc"]');
                        if (el) return el.innerText;
                        const h1 = document.querySelector('h1');
                        return h1 ? h1.innerText : "";
                    }
                """),
                timeout=TIMEOUT,
            )
        except Exception as e:
            _log(f"Ошибка caption: {e}")
            caption = ""
        _log(f"CAPTION: {caption}")

    # 1. СНГ фильтр
    # Кириллица в caption → сразу дальше.
    # Нет кириллицы → проверяем кадр через llava.
    if not is_cis(caption):
        _log("Нет кириллицы в caption → проверяю кадр через llava")
        try:
            screenshot_bytes_eng = await page.screenshot(type="jpeg", quality=70)
            frame_has_cyr = await asyncio.to_thread(
                _check_frame_has_cyrillic_text, screenshot_bytes_eng
            )
            if frame_has_cyr:
                _log("На кадре кириллица → продолжаем ✓")
            else:
                _log("На кадре нет кириллицы → скип")
                mark_checked(username, "SKIPPED")
                return False
        except Exception as e:
            _log(f"Ошибка проверки кадра: {e} → скип")
            mark_checked(username, "SKIPPED")
            return False

    # 2. Нерелевантный контент
    if is_clearly_irrelevant(caption):
        _log("Нерелевантный контент → скип")
        mark_checked(username, "SKIPPED")
        return False

    # 2b. Стоп-слова
    stop_penalty = 0
    if caption:
        cap_low = caption.lower()
        hard_hit = next((w for w in HARD_STOPWORDS if w in cap_low), None)
        if hard_hit:
            _log(f"Жёсткое стоп-слово '{hard_hit}' → скип")
            mark_checked(username, "SKIPPED")
            return False
        soft_hit = next((w for w in SOFT_STOPWORDS if w in cap_low), None)
        if soft_hit:
            _log(f"Мягкое стоп-слово '{soft_hit}' → штраф -2 (не скип)")
            stop_penalty = 2

    if caption and not has_relevant([caption]):
        _log("Нет релевантных слов в caption → скип")
        mark_checked(username, "SKIPPED")
        return False

    # 3. Скоринг caption
    cap_score = score_caption(caption)
    total_score = cap_score
    if has_target([caption]):
        total_score += 2
        _log(f"Score caption: +{cap_score} + TARGET бонус +2 (итого: {total_score})")
    else:
        _log(f"Score caption: +{cap_score} (итого: {total_score})")
    if stop_penalty:
        total_score -= stop_penalty
        _log(f"Штраф стоп-слова: -{stop_penalty} (итого: {total_score})")

    # 4. Дата по URL
    try:
        current_url = page.url
        if "/video/" in current_url:
            vid_id_str = current_url.rstrip("/").split("/video/")[-1].split("?")[0]
            vid_id = int(vid_id_str)
            ts = vid_id >> 32
            vid_date = datetime.date.fromtimestamp(ts)
            days_old = (datetime.date.today() - vid_date).days
            _log(f"Дата видео (URL): {vid_date} ({days_old} дней назад)")
            if days_old > 90:
                _log("Видео старше 3 месяцев → скип")
                mark_checked(username, "SKIPPED")
                return False
            if days_old <= 14:
                total_score += 1
                _log(f"Свежее видео ≤14 дней → +1 (итого: {total_score})")
    except Exception as e:
        _log(f"Ошибка даты URL: {e} → продолжаем")

    # 4a. llava КОНТЕНТ — только если cap_score < 3
    if cap_score >= 3:
        _log(f"Caption score {cap_score} ≥ 3 → llava пропускаем ✓")
    else:
        try:
            await page.evaluate(
                "() => { const v = document.querySelector('video'); if (v) v.pause(); }"
            )
            screenshot_bytes = await page.screenshot(type="jpeg", quality=70)
            _log(f"Caption score {cap_score} < 3 → llava анализ кадра")
            is_relevant_frame = await asyncio.to_thread(analyze_frame, screenshot_bytes)
            if is_relevant_frame:
                total_score += 2
                _log(f"llava: релевантно ✓ → +2 (итого: {total_score})")
            else:
                _log("llava: нерелевантно → продолжаем (не блокируем)")
        except Exception as e:
            _log(f"Ошибка llava: {e} → продолжаем")

    # 5. Переход в профиль
    _log(f"Захожу в профиль @{username} (score: {total_score})")
    try:
        await page.goto(
            f"https://www.tiktok.com/@{username}",
            wait_until="domcontentloaded",
            timeout=15_000,
        )
        actual_url = page.url
        if f"/@{username}" not in actual_url.lower():
            _log(f"⚠️  URL не совпадает (ожидали @{username}, получили {actual_url}) → скип")
            mark_checked(username, "SKIPPED")
            return False
        await page.wait_for_selector('[data-e2e="user-post-item"]', timeout=8_000)
    except Exception as e:
        _log(f"Ошибка перехода в профиль: {e} → скип")
        return False

    try:
        await page.evaluate("window.scrollBy(0, 600)")
    except Exception:
        pass
    await asyncio.sleep(2)

    video_elements = page.locator('[data-e2e="user-post-item"]')
    try:
        total_count = await asyncio.wait_for(video_elements.count(), timeout=TIMEOUT)
    except Exception:
        total_count = 0
    if total_count < 5:
        _log(f"Мало видео ({total_count}) → скип")
        mark_checked(username, "SKIPPED")
        return False

    # 6. Просмотры + ссылки
    views = []
    video_hrefs = []
    scan_limit = min(total_count, 15)
    for idx in range(scan_limit):
        if len(views) >= 5:
            break
        try:
            item = video_elements.nth(idx)
            views_locator = item.locator('[data-e2e="video-views"]')
            await views_locator.wait_for(state="visible", timeout=2000)
            raw = await views_locator.inner_text()
            views.append(parse_views(raw))
            href = await item.locator("a").first.get_attribute("href")
            if href:
                video_hrefs.append(href)
        except Exception:
            pass

    _log(f"Собрано просмотров: {len(views)}/5 (просканировано {scan_limit} видео)")

    if len(views) < 4:
        _log(f"Слишком мало просмотров ({len(views)}) → скип")
        mark_checked(username, "SKIPPED")
        return False

    avg_views = sum(views) / len(views)
    _log(f"AVG (5 видео): {int(avg_views)}")

    if avg_views < 1200:
        _log("AVG < 1200 → скип")
        mark_checked(username, "SKIPPED")
        return False

    views_score = score_avg_views(avg_views)
    total_score += views_score
    _log(f"Score views: +{views_score} (итого: {total_score})")

    # 7. Дата последнего видео
    if video_hrefs:
        try:
            vid_id = int(video_hrefs[0].rstrip("/").split("/video/")[-1].split("?")[0])
            ts_seconds = vid_id >> 32
            video_date = datetime.datetime.fromtimestamp(
                ts_seconds, tz=datetime.timezone.utc
            ).date()
            days_ago = (datetime.date.today() - video_date).days
            _log(f"Дата последнего видео: {video_date} ({days_ago} дней назад)")
            if days_ago > 90:
                _log("Последнее видео старше 3 месяцев → скип")
                mark_checked(username, "SKIPPED")
                return False
        except Exception as e:
            _log(f"Ошибка даты профиля: {e}")

    # 8. Частота постинга
    freq_score, freq_desc = score_posting_frequency(video_hrefs)
    total_score += freq_score
    _log(f"Частота: {freq_desc} → +{freq_score} (итого: {total_score})")

    # 9. BIO
    try:
        bio = await asyncio.wait_for(
            page.locator('[data-e2e="user-bio"]').inner_text(),
            timeout=TIMEOUT,
        )
    except Exception:
        bio = ""
    _log(f"BIO: {bio}")

    bio_sc = score_bio(bio)
    total_score += bio_sc
    _log(f"Score bio: +{bio_sc} (итого: {total_score})")

    if not has_relevant([caption, bio]):
        _log("Релевантные слова не найдены ни в caption ни в bio → скип")
        mark_checked(username, "SKIPPED")
        return False

    # 10. Gate
    _log(f"{'─'*40}")
    _log(f"ИТОГО SCORE: {total_score} | МИНИМУМ ДЛЯ AI: {MIN_SCORE_FOR_AI}")
    _log(f"{'─'*40}")
    if total_score < MIN_SCORE_FOR_AI:
        _log(f"Score {total_score} < {MIN_SCORE_FOR_AI} → пропускаем AI")
        mark_checked(username, "SKIPPED")
        return False

    # 11. AI анализ
    _log(f"★ Score {total_score} → запускаю AI")
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(analyze_account, username, bio, avg_views),
            timeout=120,
        )
        _log("AI ANALYSIS:")
        _log(result)
    except asyncio.TimeoutError:
        _log("AI таймаут → скип")
        return False
    except Exception as e:
        _log(f"Ошибка AI: {e} → скип")
        return False

    verdict = parse_ai_result(result, fallback_score=total_score)
    _log(f"AI verdict (нормализованный): {verdict}")

    # 12. Сохранение + действия
    if verdict == "TRAFFER":
        _log("")
        _log("★" * 50)
        _log(f"  НАЙДЕН ТРАФЕР: @{username}")
        _log(f"  AVG просмотры: {int(avg_views)}")
        _log(f"  SCORE: {total_score}")
        _log(f"  BIO: {bio[:80]}")
        _log("★" * 50)
        _log("")

        save_account(username, int(avg_views), total_score, bio, result)
        mark_checked(username, "TRAFFER")

        # 12a. Подписка
        await _do_follow(page, username)

        # 12b. DM
        try:
            from src.actions.dm import send_dm
            await send_dm(page, username)
        except Exception as e:
            _log(f"Ошибка DM: {e} → продолжаем")

        # 12c. Похожие — только на глубине 0 (не запускаем рекурсию)
        if _depth == 0:
            try:
                from src.similar import analyze_similar_accounts
                effective_return_url = return_url or FEED_URL
                _log(f"Анализирую похожие → возврат на: {effective_return_url}")
                await analyze_similar_accounts(page, return_url=effective_return_url)
            except Exception as e:
                _log(f"Ошибка похожих: {e} → продолжаем")
        else:
            _log("_depth=1 → пропускаем анализ похожих (защита от рекурсии)")

        return True

    else:
        _log("НЕ ТРАФЕР → СКИП")
        mark_checked(username, "NOT_TRAFFER")
        return True
