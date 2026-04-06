"""
Модуль отправки Direct Message в TikTok.
Вызывается из scroll.py после успешной подписки на трафера.
Никогда не останавливает основной процесс — любая ошибка логируется и игнорируется.

ВАЖНО: TikTok после клика на кнопку Message переходит на отдельную страницу /messages
       Поэтому ждём навигации, потом ищем поле ввода уже на новой странице.
"""

import asyncio

DM_TEXT = "привет, очень понравился твой контент. хочу предложить сотрудничество. отпиши мне на тг @perrow_official"

# Кнопка "Написать сообщение" на странице профиля
_MSG_BTN_SELECTORS = [
    '[data-e2e="message-button"]',
    'button[aria-label*="essage"]',
    'button:has-text("Message")',
    'button:has-text("Написати")',
    'button:has-text("Написать")',
    'button:has-text("Повідомлення")',
    'button:has-text("Сообщение")',
]

# Поле ввода на странице /messages (рендерится с задержкой)
_INPUT_SELECTORS = [
    '[data-e2e="message-input"]',
    '[data-e2e="dm-input"]',
    'div[class*="DraftEditor"]',
    'div[contenteditable="true"]',
    'div[contenteditable]',
    '[role="textbox"]',
    'textarea',
]

# Кнопка отправки
_SEND_BTN_SELECTORS = [
    '[data-e2e="message-send"]',
    '[data-e2e="dm-send"]',
    'button[aria-label*="Send"]',
    'button[aria-label*="send"]',
    'button:has-text("Send")',
    'button:has-text("Відправити")',
    'button:has-text("Отправить")',
    'div[data-e2e="send-message-btn"]',
]


def _log(msg: str) -> None:
    print(f"[dm] {msg}")


async def send_dm(page, username: str) -> bool:
    """
    Отправляет DM пользователю со страницы его профиля.
    Возвращает True если сообщение отправлено.
    Никогда не бросает исключений наружу.
    """
    _log(f"Попытка отправить DM → @{username}")
    profile_url = page.url  # запоминаем для возврата

    try:
        # КРИТИЧНО: имитация реального человека перед DM
        # 1. Скроллим профиль (как будто смотрим)
        _log("Имитация просмотра профиля...")
        await page.evaluate("window.scrollTo(0, 200)")
        await asyncio.sleep(0.8)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.6)
        
        # 2. Ищем кнопку Message
        _log("Ищу кнопку Message...")
        msg_btn = None
        for sel in _MSG_BTN_SELECTORS:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="visible", timeout=4000)
                msg_btn = el
                _log(f"Кнопка Message найдена ({sel})")
                break
            except Exception:
                continue

        if msg_btn is None:
            _log("Кнопка Message не найдена → пропуск")
            return False

        # 3. Движение мыши к кнопке (имитация человека)
        try:
            box = await msg_btn.bounding_box()
            if box:
                _log("Двигаю мышь к кнопке Message...")
                target_x = box["x"] + box["width"] / 2
                target_y = box["y"] + box["height"] / 2
                
                # Движение мыши в несколько этапов
                await page.mouse.move(target_x - 100, target_y - 50)
                await asyncio.sleep(0.2)
                await page.mouse.move(target_x - 50, target_y - 20)
                await asyncio.sleep(0.15)
                await page.mouse.move(target_x, target_y)
                await asyncio.sleep(0.3)
        except Exception as e:
            _log(f"Ошибка движения мыши: {e}")
        
        # 4. Кликаем кнопку Message МЫШЬЮ (не .click())
        _log("Кликаю кнопку Message мышью...")
        try:
            # Клик мышью по координатам
            await page.mouse.click(target_x, target_y)
            _log("Кликнул мышью на Message")
        except Exception as e:
            _log(f"Ошибка клика мышью: {e}, пробую .click()")
            await msg_btn.click()

        # 5. Ждём навигации (даём больше времени)
        await asyncio.sleep(4)
        current_url = page.url
        _log(f"URL после клика: {current_url}")
        
        # 6. Имитация загрузки страницы чата
        _log("Имитация загрузки чата...")
        try:
            await page.evaluate("window.scrollTo(0, 100)")
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # 3. Ищем поле ввода — ждём дольше, TikTok рендерит его лениво
        input_el = None
        _log("Ищу поле ввода (ждём до 20с)...")

        for attempt in range(10):  # 10 попыток по 2 секунды = 20 секунд максимум
            for sel in _INPUT_SELECTORS:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=2000)
                    # Проверяем что элемент реально кликабелен
                    is_visible = await el.is_visible()
                    is_enabled = await el.is_enabled()
                    if is_visible and is_enabled:
                        input_el = el
                        _log(f"Поле ввода найдено ({sel}) — попытка {attempt + 1}")
                        break
                except Exception:
                    continue

            if input_el is not None:
                break

            _log(f"Поле не найдено, жду ещё 2с... (попытка {attempt + 1}/10)")
            await asyncio.sleep(2)

        if input_el is None:
            _log("Поле ввода не найдено за 20с → пропуск")
            await _safe_return(page, profile_url)
            return False

        # 4. Фокус и ввод текста
        await input_el.click()
        await asyncio.sleep(0.5)
        await input_el.focus()
        await asyncio.sleep(0.3)

        # page.keyboard.type работает для contenteditable, element.type — нет
        await page.keyboard.type(DM_TEXT, delay=40)
        await asyncio.sleep(0.8)

        # 5. Проверяем что текст попал в поле
        try:
            field_text = await input_el.inner_text()
            if not field_text.strip():
                # Текст не попал — пробуем JS insertText
                _log("Текст не попал через keyboard.type → JS fallback")
                await page.evaluate(
                    """(el) => {
                        el.focus();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('insertText', false, arguments[1]);
                    }""",
                    await input_el.element_handle(),
                )
                await asyncio.sleep(0.5)
            else:
                _log(f"Текст в поле: {field_text[:40]!r} ✓")
        except Exception as e:
            _log(f"Проверка текста: {e} → продолжаем")

        # 6. Отправка — сначала кнопка Send, потом Enter
        sent = False

        for sel in _SEND_BTN_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2000)
                is_enabled = await btn.is_enabled()
                if is_enabled:
                    await btn.click()
                    await asyncio.sleep(1.5)
                    _log(f"Кнопка Send нажата ({sel})")
                    sent = True
                    break
            except Exception:
                continue

        if not sent:
            _log("Кнопка Send не найдена → Enter")
            await page.keyboard.press("Enter")
            await asyncio.sleep(1.5)
            sent = True

        # 7. Проверка что сообщение реально отправлено
        if sent:
            await asyncio.sleep(1.0)
            try:
                # Проверяем что поле ввода очистилось (признак отправки)
                field_after = await input_el.inner_text()
                if not field_after.strip():
                    _log(f"✓ DM отправлен успешно @{username} (поле очищено)")
                else:
                    # Поле не очистилось — проверяем наличие сообщения в чате
                    try:
                        # Ищем наше сообщение в истории чата
                        message_found = await page.evaluate(
                            """(text) => {
                                const messages = Array.from(document.querySelectorAll('[class*="message" i], [class*="Message" i]'));
                                for (const msg of messages) {
                                    if (msg.innerText && msg.innerText.includes(text.substring(0, 30))) {
                                        return true;
                                    }
                                }
                                return false;
                            }""",
                            DM_TEXT
                        )
                        if message_found:
                            _log(f"✓ DM отправлен успешно @{username} (найдено в чате)")
                        else:
                            _log(f"⚠️  Не удалось подтвердить отправку DM @{username}")
                            sent = False
                    except Exception:
                        _log(f"⚠️  Не удалось проверить отправку DM @{username}")
            except Exception as e:
                _log(f"Ошибка проверки отправки: {e}")

        # 8. Возврат на страницу профиля
        await _safe_return(page, profile_url)
        return sent

    except Exception as e:
        _log(f"Ошибка DM: {e} → продолжаем")
        await _safe_return(page, profile_url)
        return False


async def _safe_return(page, url: str) -> None:
    """Возврат на исходный URL после отправки DM."""
    if not url or url == page.url:
        return
    try:
        _log(f"Возврат на профиль: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        await asyncio.sleep(1.5)
    except Exception as e:
        _log(f"Ошибка возврата: {e}")
