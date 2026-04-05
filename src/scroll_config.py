"""
Константы и словари для TikTok парсера.
Нет зависимостей от других модулей проекта — импортируется всеми.
"""

FEED_URL = "https://www.tiktok.com/"
TIMEOUT = 2          # секунды — максимальное ожидание любого Playwright-вызова
MIN_SCORE_FOR_AI = 4

# Лента For You: анти–race с асинхронным UI TikTok (см. scroll.human_scroll)
# Случайный диапазон вместо одной константы — менее предсказуемый тайминг для UI.
FEED_SRC_STABLE_MS_MIN = 300
FEED_SRC_STABLE_MS_MAX = 700
FEED_SNAPSHOT_VERIFY_GAP_MS_MIN = 200
FEED_SNAPSHOT_VERIFY_GAP_MS_MAX = 400
FEED_WAIT_PREVIOUS_SRC_CHANGE_MS = 10_000  # этап 1: ждать смены src относительно last_completed
FEED_SRC_STABLE_MAX_WAIT_MS = 25_000

# ── Словари ───────────────────────────────────────────────────────────────────

TARGET_WORDS = [
    "деньги", "гроші",
    "заработок", "заробіток",
    "заработати", "заробити",
    "схема", "схеми",
    "доход", "дохід",
    "крипта", "крипто", "crypto",
    "арбитраж", "арбітраж",
    "пассивный", "пасивний",
    "бизнес", "бізнес",
    "трафик", "трафік",
    "заработать", "заробити",
    "инвест", "інвест",
]

MOTIVATION_WORDS = [
    "мотивация", "мотивація",
    "успех", "успіх",
    "цель", "ціль",
    "дисциплина", "дисципліна",
    "развитие", "розвиток",
    "саморазвитие", "саморозвиток",
    "мышление", "мислення",
    "привычка", "звичка",
    "продуктивность", "продуктивність",
    "фокус", "результат",
]

ALL_RELEVANT_WORDS = TARGET_WORDS + MOTIVATION_WORDS

COACH_WORDS = [
    "коуч", "coach", "наставник", "ментор", "mentor",
    "психолог", "мотиватор", "спикер", "speaker",
    "автор", "основатель", "founder",
    "помогаю", "допомагаю",
    "трансформация", "трансформація",
    "обучение", "навчання", "курс", "course",
]

IRRELEVANT_WORDS = [
    "рецепт", "рецепти", "кулінарія", "кулинария",
    "макияж", "макіяж", "makeup", "beauty",
    "танці", "танцы", "dance",
    "юмор", "гумор", "comedy", "приколы",
    "животные", "тварини", "кот", "кіт", "собака",
    "gaming", "игры", "ігри",
    "спорт", "футбол", "баскетбол",
]

# ЖЁСТКИЕ стоп-слова — мгновенный скип
HARD_STOPWORDS = [
    "ставки", "ставок", "букмекер",
    "пирамида", "піраміда",
    "быстрые деньги", "швидкі гроші",
    "реферальн",
    "партнерк",
    "марафон денег", "марафон грошей",
    "миллион за", "мільйон за",
    "пассивный доход без",
    "100$ в день", "1000$ в день",
    "без вложений", "без вкладень",
    "кейс заработка",
    "удаленка", "удалёнка", "дистанційна робота",
    "заработок от", "заробіток від",
]

# МЯГКИЕ стоп-слова — штраф -2 к score, НЕ скип
SOFT_STOPWORDS = [
    "трейдинг", "trading",
    "форекс", "forex",
    "нфт", "nft",
    "майнинг", "майнінг", "mining",
    "вебинар", "вебінар", "webinar",
]

# Объединение для обратной совместимости
STOPWORDS = HARD_STOPWORDS + SOFT_STOPWORDS

# ── JS: блокировка скролла ленты (только после фиксации ролика в Python) ─────
# Привязка к ролику: префикс src текущего playing — для отладки и согласованности
_JS_LOCK_FEED_SCROLL = """
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
        window.__feedScrollLockSrc = playing && playing.src
            ? playing.src.substring(0, 120)
            : '';

        if (playing) {
            playing.loop = true;
            playing.play();
        }

        window.__scrollBlocked = true;

        window.__blockKeyHandler = (e) => {
            if (window.__scrollBlocked &&
                (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.stopImmediatePropagation();
                e.preventDefault();
            }
        };
        document.addEventListener('keydown', window.__blockKeyHandler, true);

        window.__blockWheelHandler = (e) => {
            if (window.__scrollBlocked) {
                e.stopImmediatePropagation();
                e.preventDefault();
            }
        };
        document.addEventListener('wheel', window.__blockWheelHandler,
            { capture: true, passive: false });

        window.__blockTouchHandler = (e) => {
            if (window.__scrollBlocked) {
                e.stopImmediatePropagation();
                e.preventDefault();
            }
        };
        document.addEventListener('touchmove', window.__blockTouchHandler,
            { capture: true, passive: false });
    }
"""

# ── JS: разблокировка скролла ─────────────────────────────────────────────────
# Снимает все три блокировки (key + wheel + touch) — вызывать всегда при выходе из цикла с lock
_JS_UNLOCK_SCROLL = """
    () => {
        window.__scrollBlocked = false;
        window.__feedScrollLockSrc = null;
        if (window.__blockKeyHandler) {
            document.removeEventListener('keydown', window.__blockKeyHandler, true);
            window.__blockKeyHandler = null;
        }
        if (window.__blockWheelHandler) {
            document.removeEventListener('wheel', window.__blockWheelHandler, { capture: true });
            window.__blockWheelHandler = null;
        }
        if (window.__blockTouchHandler) {
            document.removeEventListener('touchmove', window.__blockTouchHandler, { capture: true });
            window.__blockTouchHandler = null;
        }
    }
"""
