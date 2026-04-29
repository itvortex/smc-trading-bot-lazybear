import csv
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any
from ml_logger import MLDataLogger
from notifier import notifier

logger = logging.getLogger(__name__)
ml_logger = MLDataLogger()

# ──────────────────────────────────────────────
# Таблиця параметрів для кожної монети
# (точність ціни, множник контракту)
# Додайте нову монету тут — і вона буде скрізь
# ──────────────────────────────────────────────
SYMBOL_PARAMS = {
    "BTC": {"precision": 1, "multiplier": 0.01},
    "ETH": {"precision": 2, "multiplier": 0.1},
    "XRP": {"precision": 4, "multiplier": 100.0},
    "SOL": {"precision": 3, "multiplier": 1.0},
    "BNB": {"precision": 2, "multiplier": 0.1},
    "DOGE": {"precision": 5, "multiplier": 10.0},
}
DEFAULT_PARAMS = {"precision": 3, "multiplier": 1.0}


def get_symbol_params(symbol: str) -> dict:
    """Повертає precision та multiplier для символу. Шукає по ключу монети."""
    for key, params in SYMBOL_PARAMS.items():
        if key in symbol:
            return params
    logger.warning(f"⚠️ Невідомий символ '{symbol}' — використовую параметри за замовчуванням.")
    return DEFAULT_PARAMS


class BaseStrategy(ABC):
    """
    Абстрактний базовий клас для всіх торгових стратегій.
    Містить спільну логіку: HTF bias, breakeven, закриття угоди, запис CSV.
    """

    # Мапи таймфреймів HTF
    TF_MAP = {
        '1m': '15m',
        '5m': '1h',
        '15m': '4h',
        '1h': '1d'
    }

    def __init__(self, client, risk_manager, symbol: str, timeframe: str,
                 leverage: int, rr: float, dry_run: bool):
        self.client = client
        self.risk_manager = risk_manager
        self.symbol = symbol
        self.timeframe = timeframe
        self.leverage = leverage
        self.rr = rr
        self.dry_run = dry_run

        self.pending_trade: Optional[Dict[str, Any]] = None
        self.is_active_position: bool = False
        self.is_be_set: bool = False

        # Кешуємо параметри монети один раз при створенні стратегії
        self._sym_params = get_symbol_params(symbol)

    @property
    def precision(self) -> int:
        return self._sym_params["precision"]

    @property
    def multiplier(self) -> float:
        return self._sym_params["multiplier"]

    # ──────────────────────────────────────────
    # Абстрактні методи — реалізуються в підкласах
    # ──────────────────────────────────────────

    @abstractmethod
    def analyze(self) -> Optional[Dict[str, Any]]:
        """Аналіз ринку та генерація торгових сигналів."""
        pass

    @abstractmethod
    def execute(self) -> None:
        """Головний цикл: моніторинг позиції та виконання торгівлі."""
        pass

    @abstractmethod
    def _execute_new_trade(self) -> None:
        """Логіка розміщення нового ордера (різна для сітки та single)."""
        pass

    # ──────────────────────────────────────────
    # Спільна логіка (однакова для обох стратегій)
    # ──────────────────────────────────────────

    def _get_htf_bias(self) -> str:
        """Визначає тренд на старшому таймфреймі через EMA-200."""
        htf = self.TF_MAP.get(self.timeframe, '1h')
        try:
            df_htf = self.client.fetch_ohlcv(self.symbol, htf, limit=300)
            if df_htf.empty:
                return "NEUTRAL"

            ema_200 = df_htf['close'].ewm(span=200, adjust=False).mean().iloc[-1]
            current_price = df_htf['close'].iloc[-1]

            bias = "LONG" if current_price > ema_200 else "SHORT"
            logger.info(f"HTF Context ({htf}): {bias} (Price: {current_price:.2f} | EMA: {ema_200:.2f})")
            return bias
        except Exception as e:
            logger.error(f"HTF Analysis Error ({htf}): {e}")
            return "NEUTRAL"

    def _safe_get_current_price(self) -> Optional[float]:
        """
        Безпечно отримує поточну ціну.
        ФІК #1: Перевірка що df не порожній і має хоча б 1 рядок перед iloc[-1].
        Повертає None якщо не вдалося отримати ціну.
        """
        try:
            df = self.client.fetch_ohlcv(self.symbol, '1m', limit=2)
            if df is None or df.empty or len(df) < 1:
                logger.warning(f"⚠️ [{self.symbol}] Порожній датафрейм при отриманні ціни")
                return None
            return float(df.iloc[-1]['close'])
        except IndexError as e:
            logger.warning(f"⚠️ [{self.symbol}] IndexError при отриманні ціни: {e}")
            return None
        except Exception as e:
            logger.error(f"Помилка отримання ціни [{self.symbol}]: {e}")
            return None

    def _check_tp_invalidation(self, open_orders: list) -> bool:
        """
        Перевіряє чи ціна досягла рівня TP поки позиція ще не відкрита.
        Якщо так — сетап вже неактуальний, скасовуємо всі лімітки.

        Повертає True якщо сетап було скасовано (викликач має зробити return).
        """
        if not self.pending_trade or not open_orders:
            return False

        # TP ще не розрахований (exponential стратегія до першого входу)
        tp = self.pending_trade.get('tp')
        if not tp:
            return False

        # ФІК #1: використовуємо безпечний метод отримання ціни
        current_price = self._safe_get_current_price()
        if current_price is None:
            logger.error(f"Не вдалося отримати ціну для перевірки TP [{self.symbol}]")
            return False

        action = self.pending_trade.get('action')
        invalidated = (
            (action == 'LONG' and current_price >= tp) or
            (action == 'SHORT' and current_price <= tp)
        )

        if invalidated:
            logger.info(
                f"⚠️ [{self.symbol}] Ціна ({current_price}) досягла TP ({tp}) "
                f"без входу в позицію. Скасовуємо лімітки..."
            )
            for order in open_orders:
                try:
                    self.client.exchange.cancel_order(order['id'], self.symbol)
                except Exception as e:
                    logger.error(f"Помилка скасування ордера {order['id']}: {e}")

            self._cancel_all_algo_orders()
            self.pending_trade = None

            notifier.send_message(
                f"⚠️ <b>Сетап скасовано [{self.symbol}]</b>\n"
                f"Ціна досягла рівня TP <code>{tp}</code> без входу в позицію.\n"
                f"Лімітки знято. Снайпер шукає новий сетап."
            )
            return True

        return False

    def _cancel_all_algo_orders(self):
        """Скасовує всі тригерні (algo/stop) ордери для цього символу."""
        try:
            algo_orders = self.client.exchange.fetch_open_orders(self.symbol, params={'stop': True})
            for o in algo_orders:
                try:
                    self.client.exchange.cancel_order(o['id'], self.symbol, params={'stop': True})
                except Exception as e:
                    logger.warning(f"Не вдалося скасувати algo-ордер {o['id']}: {e}")
        except Exception as e:
            logger.warning(f"Помилка отримання algo-ордерів для {self.symbol}: {e}")

    def _manage_breakeven(self, pos):
        """Переносить стоп в безубиток коли ціна досягає 50% до TP."""
        if self.is_be_set or not self.pending_trade:
            return

        # ФІК #1: використовуємо безпечний метод отримання ціни
        curr_price = self._safe_get_current_price()
        if curr_price is None:
            logger.error(f"BE Logic Error [{self.symbol}]: не вдалося отримати ціну")
            return

        try:
            t = self.pending_trade
            entry, tp, action = t['avg_entry'], t['tp'], t['action']

            # TP може бути 0 у exponential до першого спрацювання ордера
            if not tp:
                return

            be_trigger = entry + (tp - entry) * 0.5
            is_trigger = (
                (action == 'LONG' and curr_price >= be_trigger) or
                (action == 'SHORT' and curr_price <= be_trigger)
            )

            if not is_trigger:
                return

            if not self.dry_run:
                # ФІК #1: безпечно читаємо contracts з pos
                contracts_val = pos.get('contracts', 0) if isinstance(pos, dict) else getattr(pos, 'contracts', 0)
                if contracts_val in [None, '', 'NaN']:
                    logger.error(f"BE Logic Error [{self.symbol}]: некоректне значення contracts у позиції")
                    return
                contracts = float(contracts_val)
                if contracts <= 0:
                    logger.error(f"BE Logic Error [{self.symbol}]: contracts = {contracts}, пропускаємо BE")
                    return

                side = 'sell' if action == 'LONG' else 'buy'
                pos_side = 'long' if action == 'LONG' else 'short'

                self._cancel_all_algo_orders()

                # Стоп в безубиток
                self.client.create_order(
                    self.symbol, 'market', side, contracts,
                    params={'triggerPrice': entry, 'reduceOnly': True, 'tdMode': 'isolated', 'posSide': pos_side}
                )
                # Відновлюємо Тейк Профіт
                self.client.create_order(
                    self.symbol, 'market', side, contracts,
                    params={'triggerPrice': tp, 'reduceOnly': True, 'tdMode': 'isolated', 'posSide': pos_side}
                )

            self.is_be_set = True
            notifier.send_message(
                f"🛡️ <b>БЕЗУБИТОК ВСТАНОВЛЕНО</b>\n"
                f"Ціна пройшла 50% до цілі. Стоп перенесено на <code>{entry}</code>."
            )

        except Exception as e:
            logger.error(f"BE Logic Error [{self.symbol}]: {e}")

    def _handle_trade_closure(self):
        """Визначає результат закритої угоди, відправляє звіт та пише CSV."""
        if not self.pending_trade:
            return

        # ФІК #1: використовуємо безпечний метод отримання ціни
        current_price = self._safe_get_current_price()
        if current_price is None:
            logger.error(f"Не вдалося отримати ціну при закритті [{self.symbol}]")
            self.pending_trade = None
            return

        t = self.pending_trade
        action = t['action']
        entry = t['avg_entry']
        tp = t['tp']
        sl = t['sl']
        margin = t['margin']
        contracts = t['contracts']
        direction = 1 if action == 'LONG' else -1

        # Визначаємо статус по відстані до цілей
        dist_to_tp = abs(current_price - tp) if tp else float('inf')
        dist_to_sl = abs(current_price - sl)

        if tp and dist_to_tp < dist_to_sl:
            close_price = tp
            status_text = "✅ <b>ПРИБУТОК (Take Profit)</b>"
            status_csv = "Success"
        elif self.is_be_set and abs(current_price - entry) < dist_to_sl:
            close_price = entry
            status_text = "🛡️ <b>БЕЗУБИТОК (Break-Even)</b>"
            status_csv = "Break-Even"
        else:
            close_price = sl
            status_text = "🛑 <b>ЗБИТОК (Stop Loss)</b>"
            status_csv = "Failed"

        pnl_usd = direction * contracts * self.multiplier * (close_price - entry)
        roe_pct = (pnl_usd / margin) * 100 if margin > 0 else 0

        msg = (
            f"🔔 <b>ПОЗИЦІЮ ЗАКРИТО!</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🧭 {action} {self.symbol}\n"
            f"📊 Результат: {status_text}\n"
            f"💰 Чистий PnL: <b>{'+' if pnl_usd > 0 else ''}${pnl_usd:.2f}</b>\n"
            f"📈 Відсоток (ROE): <b>{'+' if roe_pct > 0 else ''}{roe_pct:.2f}%</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Снайпер продовжує сканування..."
        )
        notifier.send_message(msg)

        if not self.dry_run:
            self._write_csv(action, entry, roe_pct, pnl_usd, margin, status_csv)

        self.pending_trade = None

    def _write_csv(self, action: str, entry: float, roe_pct: float, pnl_usd: float,
                   margin: float, status_csv: str):
        """Записує результат угоди у trade_history.csv та відправляє дані для ML."""
        file_path = 'trade_history.csv'
        file_exists = os.path.isfile(file_path)
        try:
            with open(file_path, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)

                # ФІКС 1: Додали 'PosId' на перше місце в заголовок
                if not file_exists:
                    writer.writerow(
                        ['PosId', 'Date', 'Symbol', 'Action', 'Leverage', 'Margin_USD', 'ROE_Percent', 'PnL_USD',
                         'Status'])

                now = datetime.now().strftime("%Y-%m-%d %H:%M")

                # ФІКС 2: Додали порожні лапки "" на початок, щоб вирівняти колонки!
                writer.writerow([
                    "", now, self.symbol, action, f"{self.leverage}x",
                    round(margin, 2), f"{round(roe_pct, 2)}%",
                    round(pnl_usd, 2), status_csv
                ])

            # ФІКС 3: Зберігаємо результат в ML-базу
            # Якщо прибуток більший за 0 — це успіх (1), якщо ні — збиток (0)
            is_win = pnl_usd > 0
            # Використовуємо символ монети як ідентифікатор угоди
            ml_logger.log_result(self.symbol, is_win)

        except Exception as e:
            logger.error(f"Помилка запису CSV [{self.symbol}]: {e}")