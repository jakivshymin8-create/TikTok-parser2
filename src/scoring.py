"""
Чистые функции скоринга и фильтрации — без зависимостей от браузера.
Все функции синхронные и детерминированные.
"""

import datetime
import re

from src.scroll_config import (
    ALL_RELEVANT_WORDS,
    COACH_WORDS,
    IRRELEVANT_WORDS,
    MIN_SCORE_FOR_AI,
    MOTIVATION_WORDS,
    TARGET_WORDS,
)


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_caption(caption: str) -> int:
    if not caption:
        return 0
    low = caption.lower()
    score = 0
    target_hits = sum(1 for w in TARGET_WORDS if w in low)
    if target_hits >= 2:
        score += 5
    elif target_hits == 1:
        score += 3
    mot_hits = sum(1 for w in MOTIVATION_WORDS if w in low)
    if mot_hits >= 3:
        score += 3
    elif mot_hits == 2:
        score += 2
    elif mot_hits == 1:
        score += 1
    if any(w in low for w in COACH_WORDS):
        score += 2
    return score


def score_bio(bio: str) -> int:
    if not bio:
        return 0
    low = bio.lower()
    if any(w in low for w in TARGET_WORDS):
        return 4
    elif any(w in low for w in COACH_WORDS):
        return 3
    elif any(w in low for w in MOTIVATION_WORDS):
        return 1
    return 0


def score_avg_views(avg: float) -> int:
    if avg >= 50_000:
        return 4
    elif avg >= 20_000:
        return 3
    elif avg >= 5_000:
        return 2
    elif avg >= 1_200:
        return 1
    return 0


def score_posting_frequency(video_hrefs: list[str]) -> tuple[int, str]:
    today = datetime.date.today()
    dates = []
    for href in video_hrefs:
        try:
            vid_id = int(href.rstrip("/").split("/video/")[-1].split("?")[0])
            ts = vid_id >> 32
            dates.append(datetime.date.fromtimestamp(ts))
        except Exception:
            pass
    if not dates:
        return 0, "нет дат"
    recent_30 = sum(1 for d in dates if (today - d).days <= 30)
    recent_90 = sum(1 for d in dates if (today - d).days <= 90)
    if recent_30 >= 4:
        return 2, f"{recent_30}/5 видео за 30 дней — активный"
    elif recent_30 >= 2 or recent_90 >= 4:
        return 1, f"{recent_30}/5 за 30д, {recent_90}/5 за 90д — умеренный"
    return 0, f"только {recent_30}/5 за 30д — редкий постинг"


# ── Фильтры ───────────────────────────────────────────────────────────────────

def is_cis(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"[а-яёіїє]", text.lower()))


def parse_views(text: str) -> float:
    text = text.replace(",", "").strip().upper()
    try:
        if "M" in text:
            return float(text.replace("M", "")) * 1_000_000
        elif "K" in text:
            return float(text.replace("K", "")) * 1_000
        else:
            return float(text)
    except ValueError:
        return 0.0


def has_relevant(texts: list[str]) -> bool:
    combined = " ".join(texts).lower()
    return any(w in combined for w in ALL_RELEVANT_WORDS)


def has_target(texts: list[str]) -> bool:
    combined = " ".join(texts).lower()
    return any(w in combined for w in TARGET_WORDS)


def is_clearly_irrelevant(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if any(w in low for w in TARGET_WORDS):
        return False
    return any(w in low for w in IRRELEVANT_WORDS)


# ── Нормализация ответа AI ────────────────────────────────────────────────────

def parse_ai_result(raw: str, fallback_score: int = 0) -> str:
    """
    Жёсткая нормализация ответа phi3 → 'TRAFFER' | 'NOT_TRAFFER'

    Порядок проверок:
    1. Стандартный формат:  RESULT: TRAFFER / RESULT: NOT_TRAFFER
    2. Вариации без пробела: RESULT:TRAFFER, result:traffer
    3. Слово TRAFFER/NOT_TRAFFER в любом месте текста
    4. Fallback по score: score >= MIN_SCORE_FOR_AI → TRAFFER
    """
    upper = raw.strip().upper()

    # 1. NOT_TRAFFER проверяем первым — он содержит слово TRAFFER внутри
    if "RESULT: NOT_TRAFFER" in upper or "RESULT: NOT TRAFFER" in upper:
        return "NOT_TRAFFER"
    if "RESULT: TRAFFER" in upper:
        return "TRAFFER"

    # 2. Вариации: RESULT:TRAFFER, RESULT : NOT TRAFFER и т.п.
    m = re.search(r"RESULT\s*:\s*(NOT[_\s]TRAFFER|TRAFFER)", upper)
    if m:
        return "NOT_TRAFFER" if "NOT" in m.group(1) else "TRAFFER"

    # 3. Слово TRAFFER в любом месте — убираем NOT_TRAFFER чтобы не спутать
    if "NOT_TRAFFER" in upper or "NOT TRAFFER" in upper:
        return "NOT_TRAFFER"
    cleaned = re.sub(r"NOT[_\s]TRAFFER", "___", upper)
    if "TRAFFER" in cleaned:
        print("AI не дал RESULT: — нашли TRAFFER в тексте → принимаем как TRAFFER")
        return "TRAFFER"

    # 4. Fallback по score
    print(f"AI не дал RESULT: — fallback по score ({fallback_score} >= {MIN_SCORE_FOR_AI}?)")
    return "TRAFFER" if fallback_score >= MIN_SCORE_FOR_AI else "NOT_TRAFFER"
