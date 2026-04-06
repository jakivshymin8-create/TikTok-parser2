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
# НОВАЯ СТРАТЕГИЯ: блокируем ВСЁ, разблокируем только конкретные элементы
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
        window.__allowedElement = null; // Элемент который можно кликать
        
        // КРИТИЧНО: блокируем навигацию браузера
        if (!window.__navigationBlocked) {
            window.__navigationBlocked = true;
            
            window.__origHistoryBack = history.back;
            window.__origHistoryForward = history.forward;
            window.__origHistoryGo = history.go;
            window.__origHistoryPushState = history.pushState;
            window.__origHistoryReplaceState = history.replaceState;
            
            history.back = function() { console.log('[BLOCKED] history.back'); };
            history.forward = function() { console.log('[BLOCKED] history.forward'); };
            history.go = function() { console.log('[BLOCKED] history.go'); };
            history.pushState = function() { console.log('[BLOCKED] history.pushState'); };
            history.replaceState = function() { console.log('[BLOCKED] history.replaceState'); };
            
            window.__origBeforeUnload = window.onbeforeunload;
            window.onbeforeunload = function(e) {
                if (window.__scrollBlocked) {
                    console.log('[BLOCKED] beforeunload');
                    e.preventDefault();
                    return false;
                }
            };
        }

        // ГЛОБАЛЬНАЯ блокировка всех событий (разрешаем только __allowedElement)
        window.__globalBlockHandler = (e) => {
            if (!window.__scrollBlocked) return;
            
            // Проверяем разрешён ли этот элемент
            if (window.__allowedElement) {
                let target = e.target;
                // Проверяем сам элемент и его родителей
                for (let i = 0; i < 5; i++) {
                    if (!target) break;
                    if (target === window.__allowedElement) {
                        console.log('[ALLOWED] click on allowed element');
                        return; // Разрешаем
                    }
                    target = target.parentElement;
                }
            }
            
            // Блокируем всё остальное
            e.stopImmediatePropagation();
            e.preventDefault();
            return false;
        };
        
        // Вешаем на ВСЕ события
        document.addEventListener('click', window.__globalBlockHandler, true);
        document.addEventListener('mousedown', window.__globalBlockHandler, true);
        document.addEventListener('mouseup', window.__globalBlockHandler, true);
        document.addEventListener('keydown', window.__globalBlockHandler, true);
        document.addEventListener('keyup', window.__globalBlockHandler, true);
        document.addEventListener('keypress', window.__globalBlockHandler, true);
        document.addEventListener('wheel', window.__globalBlockHandler, { capture: true, passive: false });
        document.addEventListener('scroll', window.__globalBlockHandler, { capture: true, passive: false });
        document.addEventListener('touchstart', window.__globalBlockHandler, { capture: true, passive: false });
        document.addEventListener('touchmove', window.__globalBlockHandler, { capture: true, passive: false });
        document.addEventListener('touchend', window.__globalBlockHandler, { capture: true, passive: false });
    }
"""

# ── JS: разблокировка скролла ─────────────────────────────────────────────────
_JS_UNLOCK_SCROLL = """
    () => {
        window.__scrollBlocked = false;
        window.__feedScrollLockSrc = null;
        window.__allowedElement = null;
        
        // Восстанавливаем навигацию браузера
        if (window.__navigationBlocked) {
            window.__navigationBlocked = false;
            
            if (window.__origHistoryBack) history.back = window.__origHistoryBack;
            if (window.__origHistoryForward) history.forward = window.__origHistoryForward;
            if (window.__origHistoryGo) history.go = window.__origHistoryGo;
            if (window.__origHistoryPushState) history.pushState = window.__origHistoryPushState;
            if (window.__origHistoryReplaceState) history.replaceState = window.__origHistoryReplaceState;
            
            if (window.__origBeforeUnload !== undefined) {
                window.onbeforeunload = window.__origBeforeUnload;
            } else {
                window.onbeforeunload = null;
            }
        }
        
        // Удаляем глобальный обработчик
        if (window.__globalBlockHandler) {
            document.removeEventListener('click', window.__globalBlockHandler, true);
            document.removeEventListener('mousedown', window.__globalBlockHandler, true);
            document.removeEventListener('mouseup', window.__globalBlockHandler, true);
            document.removeEventListener('keydown', window.__globalBlockHandler, true);
            document.removeEventListener('keyup', window.__globalBlockHandler, true);
            document.removeEventListener('keypress', window.__globalBlockHandler, true);
            document.removeEventListener('wheel', window.__globalBlockHandler, { capture: true });
            document.removeEventListener('scroll', window.__globalBlockHandler, { capture: true });
            document.removeEventListener('touchstart', window.__globalBlockHandler, { capture: true });
            document.removeEventListener('touchmove', window.__globalBlockHandler, { capture: true });
            document.removeEventListener('touchend', window.__globalBlockHandler, { capture: true });
            window.__globalBlockHandler = null;
        }
    }
"""

# ── JS: разрешить клик на конкретный элемент ──────────────────────────────────
_JS_ALLOW_ELEMENT = """
    (selector) => {
        if (!selector) {
            window.__allowedElement = null;
            return false;
        }
        const el = document.querySelector(selector);
        if (el) {
            window.__allowedElement = el;
            console.log('[ALLOW] element:', selector);
            return true;
        }
        console.log('[ALLOW] element not found:', selector);
        return false;
    }
"""

# ── JS: запретить все клики (сбросить разрешение) ─────────────────────────────
_JS_DISALLOW_ALL = """
    () => {
        window.__allowedElement = null;
        console.log('[DISALLOW] all elements blocked');
    }
"""
