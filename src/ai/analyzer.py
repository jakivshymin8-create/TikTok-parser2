import base64
import ollama
import time


def _parse_gender_answer(raw: str) -> str:
    """
    Парсит ответ llava → 'WOMAN' | 'MAN' | 'NO_PERSON'
    Ищем ключевые слова по всему тексту ответа.
    YES/NO убраны — они не являются надёжными маркерами пола
    (llava мог ответить "NO, it's a man" → startswith("NO") давал MAN, но
     "YES, this is a woman" → startswith("YES") давал WOMAN даже для мужчины).
    Всё неясное → NO_PERSON (не блокируем).
    """
    clean = raw.strip().upper()

    # Ищем по всему тексту, не только startswith
    if any(w in clean for w in ["WOMAN", "GIRL", "FEMALE"]):
        return "WOMAN"
    if any(w in clean for w in ["MAN", "MALE", "BOY"]):
        return "MAN"

    return "NO_PERSON"


def detect_female_presenter(screenshot_bytes: bytes) -> bool:
    """
    Анализирует 1 кадр. Логика:
      - нет человека на кадре  → False (не блокируем)
      - есть человек-девушка   → True  (бан)
      - мужчина / непонятно    → False (не блокируем)

    Возвращает True только при явном WOMAN/GIRL.
    При любой ошибке → False (не блокируем).
    """
    image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    prompt = (
        "Look at this TikTok video frame.\n"
        "Identify the gender of the main person visible.\n\n"
        "Reply with EXACTLY one of these three words and nothing else:\n"
        "WOMAN - if the main visible person is female\n"
        "MAN - if the main visible person is male\n"
        "NO_PERSON - if no person is clearly visible\n\n"
        "Output only the single word. No explanation."
    )

    for attempt in range(2):
        try:
            print(f"Гендер-фильтр попытка {attempt + 1}")
            response = ollama.chat(
                model="llava:7b",
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
                options={"num_predict": 6},
            )
            raw = response["message"]["content"]
            verdict = _parse_gender_answer(raw)
            print(f"Гендер-фильтр ответ: {raw.strip()!r} → {verdict}")

            if verdict == "WOMAN":
                print("⛔ Девушка → бан")
                return True
            elif verdict == "MAN":
                print("✓ Мужчина → продолжаем")
                return False
            else:
                print("Человека нет на кадре → не блокируем")
                return False

        except Exception as e:
            print(f"Ошибка гендер-фильтра (попытка {attempt + 1}): {e}")
            time.sleep(3)

    print("Гендер-фильтр не ответил → не блокируем")
    return False


def analyze_frame(screenshot_bytes: bytes) -> bool:
    """
    Анализирует первый кадр видео через llava.
    Возвращает True если контент подходит по тематике:
    деньги, заработок, крипта, схемы, бизнес, мотивация к доходу, саморазвитие.
    При ошибке llava → пробует moondream как fallback.
    Если оба упали → возвращает True (не блокируем).
    """
    image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    prompt = (
        "You are analyzing a TikTok frame. Does this content relate to money, income, "
        "earning, crypto, business, financial schemes, or self-improvement?\n\n"
        "Say YES for ANY of these:\n"
        "- Text or speech about making money, earning income, crypto, investments\n"
        "- Business advice, entrepreneurship, passive income\n"
        "- Financial schemes, arbitrage, traffic monetization\n"
        "- Self-improvement, discipline, success mindset, personal growth\n"
        "- Motivational content about achieving goals, wealth, success\n"
        "- Person talking about life improvement, habits, mindset\n"
        "- Quotes or text overlays about success, money, discipline\n\n"
        "Say NO ONLY if clearly: dancing, comedy, food/cooking, animals, gaming, "
        "makeup/beauty tutorial, product ad, sports game, pure entertainment.\n\n"
        "When in doubt → say YES.\n"
        "Reply ONE word only: YES or NO."
    )

    # Выгружаем phi3 из RAM перед llava чтобы не было OOM
    try:
        ollama.chat(model="phi3", messages=[], keep_alive=0)
    except Exception:
        pass
    time.sleep(1)

    for attempt in range(3):
        try:
            print(f"llava попытка {attempt + 1}")
            response = ollama.chat(
                model="llava:7b",
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
                options={"num_predict": 10},
            )
            answer = response["message"]["content"].strip().upper()
            print(f"llava ответ: {answer}")
            return "YES" in answer
        except Exception as e:
            err = str(e)
            print(f"Ошибка llava (попытка {attempt + 1}): {e}")
            if "500" in err or "terminated" in err or "runner" in err.lower():
                print("llava runner упал → ждём 8с")
                time.sleep(8)
            else:
                time.sleep(3)
        finally:
            # Выгружаем после каждой попытки независимо от результата
            _unload_llava()

    # Fallback: moondream
    print("llava не ответил 3 раза → пробую moondream fallback")
    try:
        response = ollama.chat(
            model="moondream",
            messages=[{
                "role": "user",
                "content": (
                    "Is this content about money, earning, crypto, business, "
                    "or self-improvement? Reply YES or NO only."
                ),
                "images": [image_b64],
            }],
            options={"num_predict": 5},
        )
        answer = response["message"]["content"].strip().upper()
        print(f"moondream ответ: {answer}")
        return "YES" in answer
    except Exception as e:
        print(f"moondream fallback тоже упал: {e} → пропускаем фильтр кадра")

    return True


def _unload_llava() -> None:
    """Выгружает llava из RAM после использования."""
    try:
        ollama.chat(model="llava:7b", messages=[], keep_alive=0)
    except Exception:
        pass


def analyze_account(username: str, bio: str, avg_views: float) -> str:
    """
    Анализирует TikTok аккаунт через phi3.
    Вызывается только после прохождения всех фильтров и score gate.
    Возвращает строку с RESULT и REASON, или "ERROR".
    """
    prompt = (
        f"TikTok @{username}. BIO: {bio[:150]}. AVG views: {int(avg_views)}.\n\n"
        "Classify this TikTok account. Answer TRAFFER or NOT_TRAFFER.\n\n"
        "TRAFFER — account covers ANY of these:\n"
        "- Self-improvement, personal growth, discipline, motivation, success mindset\n"
        "- Life coaching, productivity, habits, goal-setting, psychology\n"
        "- Making money, income, investments, crypto, business, passive income\n"
        "- Confidence, mindset, self-development, саморазвитие\n\n"
        "NOT_TRAFFER — ONLY if account is clearly: pure comedy/memes, cooking recipes,\n"
        "makeup tutorials, sports games, dancing, animals — with ZERO self-improvement.\n\n"
        "IMPORTANT: A person who talks about cinema, astrophysics AND self-development = TRAFFER.\n"
        "A motivational girl = TRAFFER. Personal growth content = TRAFFER.\n"
        "When in doubt → TRAFFER.\n\n"
        "Reply ONLY:\n"
        "RESULT: TRAFFER or NOT_TRAFFER\n"
        "REASON: one line"
    )
    for attempt in range(3):
        try:
            print(f"AI попытка {attempt + 1}")
            response = ollama.chat(
                model="phi3",
                messages=[{"role": "user", "content": prompt}]
            )
            return response["message"]["content"]
        except Exception as e:
            err = str(e)
            print(f"Ошибка AI: {e}")
            if "500" in err or "terminated" in err or "runner" in err.lower():
                print("phi3 runner упал → ждём 8с")
                time.sleep(8)
            else:
                time.sleep(2)

    return "ERROR"