import time
import signal
import threading
import logging
import os
from logging.handlers import RotatingFileHandler
from notifier import *

# --- НАЛАШТУВАННЯ ЛОГІВ ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler = RotatingFileHandler('bot_activity.log', maxBytes=5 * 1024 * 1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger("Bot-Core")

# --- ІМПОРТИ МОДУЛІВ ---
from bot_state import *
from background_scanner import market_scanner_thread

# Підключення обробників Telegram (це реєструє всі кнопки)
from tg_handlers.general import *
from tg_handlers.builder import *
from tg_handlers.analytics import *


def graceful_shutdown(signum=None, frame=None):
    """Коректна зупинка бота: скасовуємо ордери, зберігаємо стан, виходимо."""
    logger.info("🛑 Отримано сигнал зупинки. Починаємо graceful shutdown...")
    try:
        notifier.send_message("🛑 <b>Сервер зупинено.</b> Бот офлайн. 😴", parse_mode="HTML")
    except Exception:
        pass

    try:
        bot_state.stop_all_snipers()
        logger.info("✅ Всі снайпери зупинені, ордери скасовані.")
    except Exception as e:
        logger.error(f"Помилка при зупинці снайперів: {e}")

    # ⚠️ НЕ видаляємо trade_history.csv — це важливі дані!
    # Якщо потрібно очищення — робіть це вручну або через команду бота.

    logger.info("👋 Бот завершив роботу.")
    os._exit(0)


def run_bot():
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    logger.info("🚀 Запуск бота...")

    # Фоновий сканер ринку
    scanner_thread = threading.Thread(target=market_scanner_thread, daemon=True, name="MarketScanner")
    scanner_thread.start()

    # Telegram polling у окремому потоці (використовуємо захищений метод)
    polling_thread = threading.Thread(target=notifier.start_polling, daemon=True, name="TelegramPolling")
    polling_thread.start()

    notifier.send_message(
        "🟢 <b>Система багатоснайперного сканування запущена!</b>\n\n"
        "Бот очікує ваших вказівок. Натисніть «⚙️ Снайпери» щоб обрати та запустити активи.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

    # Утримання головного потоку живим + моніторинг здоров'я потоків
    while True:
        time.sleep(30)

        # Якщо polling впав — намагаємось перезапустити
        if not polling_thread.is_alive():
            logger.warning("⚠️ Telegram polling потік впав. Перезапускаємо...")
            polling_thread = threading.Thread(target=notifier.start_polling, daemon=True, name="TelegramPolling")
            polling_thread.start()


if __name__ == "__main__":
    run_bot()