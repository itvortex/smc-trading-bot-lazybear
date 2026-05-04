import time
import logging
import threading
import bot_state

logger = logging.getLogger("Bot-Core")

SCAN_INTERVAL_SEC = 60  # Інтервал між сканами в секундах


def _run_strategy_safe(s_id, strategy):
    """Запускає одну стратегію в ізольованому потоці з заміром часу"""
    start = time.time()
    try:
        strategy.execute()
        elapsed = round(time.time() - start, 2)
        logger.debug(f"[{strategy.symbol}] виконано за {elapsed}s")
    except Exception as e:
        logger.error(f"Помилка сканування {strategy.symbol}: {e}")


def market_scanner_thread():
    """Безперервно опитує всі активні стратегії у фоні.

    Покращення порівняно з оригіналом:
    - Адаптивний sleep: чекаємо рівно SCAN_INTERVAL від початку циклу,
      а не фіксовані 60с після завершення — тобто цикл завжди рівно 60с.
    - Кожна стратегія запускається у власному daemon-потоці, тому
      одна зависла стратегія не блокує решту.
    - Логується кількість активних стратегій на кожен скан.
    """
    logger.info("🔍 Фоновий сканер запущено.")

    while True:
        cycle_start = time.time()

        if bot_state.GLOBAL_RUNNING and bot_state.active_strategies:
            with bot_state.strategies_lock:
                strategies_snapshot = list(bot_state.active_strategies.items())
            logger.info(f"Scanning market — активних стратегій: {len(strategies_snapshot)}")

            threads = []
            for s_id, strategy in strategies_snapshot:
                t = threading.Thread(
                    target=_run_strategy_safe,
                    args=(s_id, strategy),
                    daemon=True
                )
                t.start()
                threads.append(t)

            # Чекаємо завершення всіх потоків, але не більше 55с
            # (щоб залишити буфер до наступного циклу)
            for t in threads:
                t.join(timeout=55)

        # Адаптивний sleep: компенсуємо час виконання
        elapsed = time.time() - cycle_start
        sleep_time = max(0, SCAN_INTERVAL_SEC - elapsed)
        time.sleep(sleep_time)