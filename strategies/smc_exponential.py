import logging
import uuid
from typing import Optional, Dict, Any

from telebot import types

from .base_strategy import BaseStrategy
from risk_manager import RiskManager
from indicators import find_smc_indicators
from notifier import notifier, TRADE_DETAILS_CACHE

logger = logging.getLogger(__name__)


class SMCExponentialStrategy(BaseStrategy):

    def __init__(self, client, risk_manager: RiskManager, symbol: str,
                 timeframe: str = '1m', leverage: int = 10,
                 rr: float = 2.0, dry_run: bool = False):
        super().__init__(client, risk_manager, symbol, timeframe, leverage, rr, dry_run)
        self.current_contracts: float = 0.0

    # ──────────────────────────────────────────
    # Аналіз
    # ──────────────────────────────────────────

    def analyze(self) -> Optional[Dict[str, Any]]:
        bias = self._get_htf_bias()
        df = self.client.fetch_ohlcv(self.symbol, self.timeframe, limit=300)
        if df.empty:
            return None

        df = find_smc_indicators(df)
        recent = df.tail(15)
        current = df.iloc[-1]

        if bias == "LONG":
            bull_obs = recent[recent['bullish_ob'] == True]
            if not bull_obs.empty:
                ob = bull_obs.iloc[-1]
                if ob['low'] < current['close'] < (ob['high'] * 1.005) and recent['fvg_bullish'].any():
                    logger.info("🔥 Знайдено Bullish Setup: OB + FVG!")
                    return self._prepare_signal('LONG', ob)

        if bias == "SHORT":
            bear_obs = recent[recent['bearish_ob'] == True]
            if not bear_obs.empty:
                ob = bear_obs.iloc[-1]
                if (ob['low'] * 0.995) < current['close'] < ob['high'] and recent['fvg_bearish'].any():
                    logger.info("❄️ Знайдено Bearish Setup: OB + FVG!")
                    return self._prepare_signal('SHORT', ob)

        return None

    def _prepare_signal(self, action: str, ob) -> Dict[str, Any]:
        if action == 'LONG':
            poi_start, poi_end = float(ob['high']), float(ob['low'])
            sl = poi_end * 0.998
        else:
            poi_start, poi_end = float(ob['low']), float(ob['high'])
            sl = poi_end * 1.002

        return {'action': action, 'poi_start': poi_start, 'poi_end': poi_end, 'sl': sl}

    # ──────────────────────────────────────────
    # Головний цикл
    # ──────────────────────────────────────────

    def execute(self) -> None:
        try:
            positions = self.client.exchange.fetch_positions([self.symbol])
            pos = next((p for p in positions if float(p.get('contracts', 0)) > 0), None)
            open_orders = self.client.exchange.fetch_open_orders(self.symbol)
        except Exception as e:
            logger.error(f"Execution API Error [{self.symbol}]: {e}")
            return

        # 1. МОНІТОРИНГ АКТИВНОЇ ПОЗИЦІЇ
        if pos:
            current_pos_size = float(pos['contracts'])

            # Якщо спрацювала нова лімітка — оновлюємо глобальний SL/TP
            if current_pos_size > self.current_contracts:
                self._update_global_sl_tp(pos)
                self.current_contracts = current_pos_size

                if not self.is_active_position:
                    self.is_active_position = True
                    notifier.send_message(
                        f"🔔 <b>Ордери спрацювали!</b>\n"
                        f"Позиція {self.symbol} відкрита. Глобальний захист активовано."
                    )

            self._manage_breakeven(pos)
            return

        # 2. ПОЗИЦІЯ ЗАКРИЛАСЯ
        if self.is_active_position and not pos:
            self.is_active_position = False
            self.is_be_set = False
            self.current_contracts = 0.0

            try:
                for order in self.client.exchange.fetch_open_orders(self.symbol):
                    self.client.exchange.cancel_order(order['id'], self.symbol)
            except Exception as e:
                logger.error(f"Помилка скасування ордерів при закритті [{self.symbol}]: {e}")

            self._cancel_all_algo_orders()
            self._handle_trade_closure()
            return

        # 3. СЕТАП З ВІДКРИТИМИ ЛІМІТКАМИ — ПЕРЕВІРКА ІНВАЛІДАЦІЇ
        if not pos and len(open_orders) > 0:
            if self._check_tp_invalidation(open_orders):
                return

        # 4. ПОШУК НОВОГО СЕТАПУ
        if not pos and len(open_orders) == 0:
            self.pending_trade = None
            self.current_contracts = 0.0
            self._execute_new_trade()

    # ──────────────────────────────────────────
    # Оновлення SL/TP після зміни об'єму позиції
    # ──────────────────────────────────────────

    def _update_global_sl_tp(self, pos):
        if not self.pending_trade or self.dry_run:
            return

        try:
            self._cancel_all_algo_orders()

            real_avg_entry = float(pos['entryPrice'])
            contracts = float(pos['contracts'])
            action = 'LONG' if str(pos['side']).upper() == 'LONG' else 'SHORT'
            close_side = 'sell' if action == 'LONG' else 'buy'
            pos_side = 'long' if action == 'LONG' else 'short'

            sl_p = self.pending_trade['sl']

            if action == 'LONG':
                risk = real_avg_entry - sl_p
                tp_p = real_avg_entry + (risk * self.rr)
            else:
                risk = sl_p - real_avg_entry
                tp_p = real_avg_entry - (risk * self.rr)

            tp_p = round(tp_p, self.precision)
            sl_p = round(sl_p, self.precision)
            real_avg_entry = round(real_avg_entry, self.precision)

            self.pending_trade['avg_entry'] = real_avg_entry
            self.pending_trade['tp'] = tp_p

            # Take Profit
            self.client.create_order(
                self.symbol, 'market', close_side, contracts,
                params={'triggerPrice': tp_p, 'reduceOnly': True, 'tdMode': 'isolated', 'posSide': pos_side}
            )
            # Stop Loss
            self.client.create_order(
                self.symbol, 'market', close_side, contracts,
                params={'triggerPrice': sl_p, 'reduceOnly': True, 'tdMode': 'isolated', 'posSide': pos_side}
            )

            logger.info(
                f"🔄 Глобальні SL/TP оновлено [{self.symbol}]. "
                f"Об'єм: {contracts}. Сер. ціна: {real_avg_entry}. TP: {tp_p}, SL: {sl_p}"
            )
        except Exception as e:
            logger.error(f"Помилка оновлення глобального SL/TP [{self.symbol}]: {e}")

    # ──────────────────────────────────────────
    # Новий трейд
    # ──────────────────────────────────────────

    def _execute_new_trade(self) -> None:
        signal = self.analyze()
        if not signal:
            return

        current_price = float(self.client.fetch_ohlcv(self.symbol, '1m', limit=1).iloc[-1]['close'])
        is_long = signal['action'] == 'LONG'
        pos_side = 'long' if is_long else 'short'
        entry_side = 'buy' if is_long else 'sell'

        if not self.dry_run:
            try:
                self.client.set_leverage(self.leverage, self.symbol, margin_mode='isolated', pos_side=pos_side)
            except Exception as e:
                logger.error(f"Помилка встановлення плеча [{self.symbol}]: {e}")

        req = self.risk_manager.calculate_requirements(
            self.symbol, current_price, leverage=self.leverage
        )
        required_margin = req.get('required_margin', 0)
        if required_margin and not self.risk_manager.can_afford(self.client, required_margin):
            logger.warning(f"💸 Недостатньо коштів [{self.symbol}]. Сетап пропущено.")
            return

        # 🔥 ЗМІНА ТУТ: Додано параметр num_orders=3, щоб RiskManager знав, скільки ліміток ставити
        entry_grid = self.risk_manager.calculate_exponential_grid(
            self.symbol, current_price, signal['poi_start'], signal['poi_end'], num_orders=3
        )

        if not entry_grid:
            return

        total_contracts = sum(int(o.amount) for o in entry_grid if int(o.amount) > 0)
        if total_contracts == 0:
            return

        entry_details = ""
        successful_orders = 0

        for i, order in enumerate(entry_grid):
            amt = int(order.amount)
            if amt <= 0:
                continue

            if not self.dry_run:
                try:
                    self.client.create_order(
                        self.symbol, 'limit', entry_side, amt, order.price,
                        params={'tdMode': 'isolated', 'posSide': pos_side}
                    )
                    successful_orders += 1
                except Exception as e:
                    logger.error(f"Помилка виставлення лімітки {i + 1} [{self.symbol}]: {e}")
            else:
                successful_orders += 1

            entry_details += f"   <b>L{i + 1}:</b> <code>{order.price}</code> | {amt} контр.\n"

        if successful_orders == 0:
            return

        sl_p = round(float(signal['sl']), self.precision)
        avg_entry_est = round(
            sum(o.price * int(o.amount) for o in entry_grid if int(o.amount) > 0) / total_contracts,
            self.precision
        )
        margin = (total_contracts * avg_entry_est * self.multiplier) / self.leverage

        # 🔥 ВАЖЛИВИЙ ФІКС: Одразу розраховуємо орієнтовний TP для роботи перевірки інвалідації!
        if is_long:
            risk_est = avg_entry_est - sl_p
            tp_est = avg_entry_est + (risk_est * self.rr)
        else:
            risk_est = sl_p - avg_entry_est
            tp_est = avg_entry_est - (risk_est * self.rr)

        self.is_be_set = False
        self.pending_trade = {
            'action': signal['action'],
            'avg_entry': avg_entry_est,
            'contracts': total_contracts,
            'margin': margin,
            'tp': round(tp_est, self.precision),  # Було 0.0
            'sl': sl_p,
            'limits_count': successful_orders
        }

        details_text = (
            f"📝 <b>ДЕТАЛІ ПОЗИЦІЇ (Очікування)</b>\n━━━━━━━━━━━━━━━\n"
            f"🧭 <b>Напрямок:</b> {'📈' if is_long else '📉'} <b>{signal['action']} {self.symbol}</b>\n"
            f"📂 <b>Сітка ордерів на вхід:</b>\n{entry_details}\n"
            f"<i>Глобальні TP та SL виставляться автоматично після відкриття позиції!</i>\n"
            f"━━━━━━━━━━━━━━━\n💰 Маржа: <b>~${margin:.2f}</b>\n⚖️ R:R = 1:{self.rr}"
        )

        trade_id = str(uuid.uuid4())[:8]
        TRADE_DETAILS_CACHE[trade_id] = details_text
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📊 Детальна інформація", callback_data=f"info|{trade_id}"))

        notifier.send_message(
            f"{'📈' if is_long else '📉'} <b>[СЕТАП] {signal['action']} {self.symbol} ({self.timeframe})</b>\n"
            f"Лімітки розставлено. Чекаємо входу...",
            reply_markup=markup
        )