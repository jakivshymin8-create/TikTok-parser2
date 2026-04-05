import ollama

DEFAULT_KEYWORDS = [
    "money",
    "motivation",
    "hustle",
    "crypto",
    "мотивация",
    "мотивація",
    "деньги",
    "заробіток",
    "заработок"
]


def generate_keywords():
    prompt = """
    Сгенерируй 20 ключевых слов и хештегов для TikTok по темам:
    крипта, деньги, заработок, мотивация, схемы, арбитраж
    Верни список через запятую, без объяснений
    """
    response = ollama.chat(
        model="llama3",
        messages=[{"role": "user", "content": prompt}]
    )
    text = response["message"]["content"]
    keywords = [k.strip().lower() for k in text.split(",") if k.strip()]
    return keywords if keywords else DEFAULT_KEYWORDS
