import os
import json
import logging
from okx_client import OKXClient

logger = logging.getLogger("Bot-Core")

# --- СТАН БОТА ---
GLOBAL_RUNNING = True
DRY_RUN = False
client = OKXClient()

# Словники для паралельної роботи снайперів
active_strategies = {}
risk_managers = {}
user_builder = {}

SNIPERS_FILE = 'snipers.json'


# ──────────────────────────────────────────────
# Збереження / завантаження конфігурацій снайперів
# ──────────────────────────────────────────────

def load_snipers():
    if os.path.exists(SNIPERS_FILE):
        with open(SNIPERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    default_data = {
        "active": [],   # Список s_id які були активні при останньому shutdown
        "list": {
            "default_eth": {
                "name": "ETH Sniper",
                "symbol": "ETH/USDT:USDT",
                "position": 100.0,
                "leverage": 10,
                "rr": 2.0,
                "timeframe": "1m",
                "strat_type": "exponential"
            }
        }
    }
    save_snipers(default_data)
    return default_data


def save_snipers(data):
    with open(SNIPERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def save_active_state():
    """Зберігає список зараз активних s_id у snipers.json.
    Викликати при запуску/зупинці снайпера."""
    data = load_snipers()
    data["active"] = list(active_strategies.keys())
    save_snipers(data)
    logger.debug(f"💾 Збережено активні снайпери: {data['active']}")


snipers_data = load_snipers()


# ──────────────────────────────────────────────
# Робота з ордерами
# ──────────────────────────────────────────────

def safe_cancel_all_orders(symbol: str):
    """Надійний спосіб скасування всіх ордерів по одній монеті."""
    try:
        regular_orders = client.exchange.fetch_open_orders(symbol)
        for order in regular_orders:
            try:
                client.exchange.cancel_order(order['id'], symbol)
            except Exception as e:
                logger.error(f"Не вдалося скасувати лімітку {order['id']} для {symbol}: {e}")

        try:
            algo_orders = client.exchange.fetch_open_orders(symbol, params={'stop': True})
            for order in algo_orders:
                try:
                    client.exchange.cancel_order(order['id'], symbol, params={'stop': True})
                except Exception as e:
                    logger.error(f"Не вдалося скасувати алго-ордер {order['id']} для {symbol}: {e}")
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Глобальна помилка при скасуванні ордерів {symbol}: {e}")
        raise e


# ──────────────────────────────────────────────
# Керування снайперами
# ──────────────────────────────────────────────

def start_sniper(s_id: str, strategy_obj):
    """Реєструє стратегію як активну та зберігає стан."""
    active_strategies[s_id] = strategy_obj
    save_active_state()
    logger.info(f"🟢 Снайпер [{s_id}] запущено: {strategy_obj.symbol}")


def stop_sniper(s_id: str):
    """Зупиняє один снайпер, скасовує його ордери та зберігає стан."""
    strategy = active_strategies.pop(s_id, None)
    if strategy:
        try:
            safe_cancel_all_orders(strategy.symbol)
        except Exception as e:
            logger.error(f"Помилка скасування ордерів при зупинці [{s_id}]: {e}")
        save_active_state()
        logger.info(f"🔴 Снайпер [{s_id}] зупинено: {strategy.symbol}")
    else:
        logger.warning(f"⚠️ Спроба зупинити невідомий снайпер [{s_id}]")


def stop_all_snipers():
    """Зупиняє всі активні снайпери та очищує стан."""
    global active_strategies
    for s_id, strategy in list(active_strategies.items()):
        try:
            safe_cancel_all_orders(strategy.symbol)
        except Exception as e:
            logger.error(f"Помилка зупинки [{s_id}]: {e}")

    active_strategies.clear()
    save_active_state()  # Зберігаємо порожній список
    logger.info("🛑 Всі снайпери зупинені.")