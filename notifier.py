import telebot
import os
import time
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("Bot-Core")

# Кеш для детальної інформації про угоди
TRADE_DETAILS_CACHE = {}


class TelegramNotifier:
    def __init__(self):
        token = os.getenv("TELEGRAM_TOKEN")

        if not token:
            raise ValueError(
                "❌ ПОМИЛКА: TELEGRAM_TOKEN не знайдено! Переконайтеся, що файл .env існує і містить токен.")

        self.bot = telebot.TeleBot(token, threaded=False)
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self._verify_connection()

    def _verify_connection(self):
        """Перевіряє з'єднання з Telegram при старті. Не крашить бот якщо немає з'єднання."""
        try:
            me = self.bot.get_me()
            logger.info(f"✅ Telegram підключено: @{me.username}")
        except Exception as e:
            # Просто логуємо — бот продовжує роботу без Telegram
            logger.warning(f"⚠️ Telegram недоступний при старті: {e}. Бот продовжить без сповіщень.")

    def send_message(self, text, reply_markup=None, parse_mode="HTML", retries=3):
        """Відправка повідомлення з автоматичним retry (до 3 спроб)"""
        for attempt in range(1, retries + 1):
            try:
                return self.bot.send_message(
                    self.chat_id,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            except telebot.apihelper.ApiTelegramException as e:
                # 401 = невалідний токен, 403 = бот заблокований — не має сенсу retry
                if e.error_code in (401, 403):
                    logger.error(f"❌ Telegram API помилка {e.error_code}: {e.description}. Retry марний.")
                    return None
                logger.warning(f"Telegram помилка (спроба {attempt}/{retries}): {e}")
            except Exception as e:
                logger.warning(f"Помилка відправки TG (спроба {attempt}/{retries}): {e}")

            if attempt < retries:
                time.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s

        logger.error(f"❌ Не вдалося відправити повідомлення після {retries} спроб.")
        return None

    def _flush_pending_updates(self):
        """
        Очищає всі старі повідомлення що накопичились поки бот був офлайн.
        Робиться через get_updates з offset=-1 щоб отримати останній update_id,
        потім ще раз з offset=last+1 щоб підтвердити що всі прочитані.
        """
        try:
            updates = self.bot.get_updates(offset=-1, timeout=5)
            if updates:
                last_id = updates[-1].update_id
                self.bot.get_updates(offset=last_id + 1, timeout=5)
                logger.info(f"🧹 Очищено старих повідомлень до update_id={last_id}")
            else:
                logger.info("🧹 Нових повідомлень в черзі немає.")
        except Exception as e:
            logger.warning(f"⚠️ Не вдалось очистити чергу Telegram: {e}")

    def start_polling(self):
        """Запускає polling з захистом від краш-петлі.

        ReadTimeout та ConnectionError — ігноруються, polling перезапускається сам.
        401/403 — зупиняємо одразу, немає сенсу retry.
        Перед стартом очищаємо чергу — виправляє баг коли команда
        "Вимкнути бота" виконується при кожному запуску.
        """
        import requests.exceptions

        # Очищаємо всі накопичені повідомлення перед стартом
        self._flush_pending_updates()

        while True:
            try:
                self.bot.infinity_polling(
                    timeout=30,
                    long_polling_timeout=30,
                    logger_level=logging.WARNING,
                    restart_on_change=False,
                )
                # Якщо infinity_polling завершився без виключення — виходимо
                break

            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code in (401, 403):
                    logger.error(f"❌ Telegram API {e.error_code}: токен невалідний або бот заблокований. Polling зупинено.")
                    break  # Retry марний — токен треба виправити
                logger.warning(f"⚠️ Telegram API помилка {e.error_code}, перезапуск через 5с...")
                time.sleep(5)

            except (requests.exceptions.ReadTimeout,
                    requests.exceptions.ConnectionError) as e:
                # Мережева проблема — просто чекаємо і пробуємо знову
                logger.warning(f"⚠️ Мережева помилка Telegram (ReadTimeout/Connection), перезапуск через 10с...")
                time.sleep(10)

            except Exception as e:
                logger.error(f"❌ Невідома помилка polling: {e}. Перезапуск через 15с...")
                time.sleep(15)


# Створюємо єдиний екземпляр для всієї програми
notifier = TelegramNotifier()