import logging
import uuid
from typing import Optional, Dict, Any

from telebot import types

from .base_strategy import BaseStrategy
from risk_manager import RiskManager
from indicators import find_smc_indicators
from notifier import notifier, TRADE_DETAILS_CACHE

logger = logging.getLogger(__name__)


class SMCSingleStrategy(BaseStrategy):

    def __init__(self, client, risk_manager: RiskManager, symbol: str,
                 timeframe: str = '1m', rr: float = 2.0,
                 dry_run: bool = False):
        super().__init__(client, risk_manager, symbol, timeframe,
                         leverage=10, rr=rr, dry_run=dry_run)

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
            risk = poi_start - sl
            tp = poi_start + (risk * self.rr)
        else:
            poi_start, poi_end = float(ob['low']), float(ob['high'])
            sl = poi_end * 1.002
            risk = sl - poi_start
            tp = poi_start - (risk * self.rr)

        return {'action': action, 'poi_start': poi_start, 'poi_end': poi_end, 'sl': sl, 'tp': tp}

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

        # 1. МОНІТОРИНГ ПОЗИЦІЇ
        if pos:
            if not self.is_active_position:
                self.is_active_position = True

                # ФІК #5: TP/SL вже прикріплені через attachAlgoOrds при виставленні лімітки.
                # Перевіряємо algo-ордери — якщо їх немає (рідкісний fallback),
                # виставляємо вручну. У нормальному сценарії вони вже активні.
                algo_orders = []
                try:
                    algo_orders = self.client.exchange.fetch_open_orders(
                        self.symbol, params={'stop': True}
                    )
                except Exception:
                    pass

                if not algo_orders:
                    # Fallback — виставляємо TP/SL вручну ОДРАЗУ при відкритті позиції
                    logger.warning(
                        f"⚠️ TP/SL не знайдено після входу [{self.symbol}] — "
                        f"виставляємо вручну негайно."
                    )
                    self._set_tp_sl_immediately(pos)
                else:
                    notifier.send_message(
                        f"🔔 <b>Ордер спрацював!</b>\n"
                        f"Позиція {self.symbol} відкрита. TP/SL активні."
                    )
            self._manage_breakeven(pos)
            return

        # 2. ПОЗИЦІЯ ЗАКРИЛАСЯ
        if self.is_active_position and not pos:
            self.is_active_position = False
            self.is_be_set = False

            try:
                for order in open_orders:
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

        # 4. ПОШУК СЕТАПУ
        if not pos and len(open_orders) == 0:
            self.pending_trade = None
            self._execute_new_trade()

    # ──────────────────────────────────────────
    # ФІК #5: Виставлення TP/SL ОДРАЗУ після відкриття позиції
    # ──────────────────────────────────────────

    def _set_tp_sl_immediately(self, pos):
        """
        ФІК #5: Виставляє TP і SL негайно після виявлення відкритої позиції.
        Викликається тільки якщо attachAlgoOrds не спрацював (fallback).
        Після успішного виставлення — відправляє повідомлення в TG.
        """
        if not self.pending_trade or self.dry_run:
            return

        try:
            contracts = float(pos['contracts'])
            action = 'LONG' if str(pos['side']).upper() == 'LONG' else 'SHORT'
            close_side = 'sell' if action == 'LONG' else 'buy'
            pos_side = 'long' if action == 'LONG' else 'short'

            tp_p = self.pending_trade['tp']
            sl_p = self.pending_trade['sl']

            # Take Profit — виставляємо першим (важливіший)
            self.client.create_order(
                self.symbol, 'market', close_side, contracts,
                params={
                    'triggerPrice': float(tp_p),
                    'reduceOnly': True,
                    'tdMode': 'isolated',
                    'posSide': pos_side
                }
            )
            # Stop Loss
            self.client.create_order(
                self.symbol, 'market', close_side, contracts,
                params={
                    'triggerPrice': float(sl_p),
                    'reduceOnly': True,
                    'tdMode': 'isolated',
                    'posSide': pos_side
                }
            )

            logger.info(
                f"✅ TP/SL виставлено негайно [{self.symbol}]. "
                f"TP: {tp_p}, SL: {sl_p}"
            )
            notifier.send_message(
                f"🔔 <b>Ордер спрацював!</b>\n"
                f"Позиція {self.symbol} відкрита.\n"
                f"✅ TP: <code>{tp_p}</code>\n"
                f"🛑 SL: <code>{sl_p}</code>\n"
                f"⚠️ Захист виставлено через fallback (attachAlgoOrds не спрацював)."
            )

        except Exception as e:
            logger.error(f"Помилка виставлення TP/SL після входу [{self.symbol}]: {e}")
            notifier.send_message(
                f"🚨 <b>УВАГА! [{self.symbol}]</b>\n"
                f"Позиція відкрита але TP/SL НЕ ВИСТАВЛЕНІ!\n"
                f"Причина: {e}\n"
                f"Перевірте позицію вручну!"
            )

    # Стара назва методу залишена для зворотної сумісності
    def _update_global_sl_tp(self, pos):
        """Псевдонім для _set_tp_sl_immediately (зворотна сумісність)."""
        self._set_tp_sl_immediately(pos)

    # ──────────────────────────────────────────
    # Новий трейд
    # ──────────────────────────────────────────

    def _execute_new_trade(self) -> None:
        signal = self.analyze()
        if not signal:
            return

        # ФІК #5: використовуємо безпечний метод отримання ціни з base_strategy
        current_price = self._safe_get_current_price()
        if current_price is None:
            logger.error(f"[{self.symbol}] Не вдалося отримати поточну ціну для нового трейду")
            return

        is_long = signal['action'] == 'LONG'
        pos_side = 'long' if is_long else 'short'
        entry_side = 'buy' if is_long else 'sell'

        if not self.dry_run:
            try:
                self.client.set_leverage(self.leverage, self.symbol, margin_mode='isolated', pos_side=pos_side)
            except Exception as e:
                logger.error(f"Помилка встановлення плеча [{self.symbol}]: {e}")

        entry_grid = self.risk_manager.calculate_single_order(
            self.symbol, current_price, signal['poi_start']
        )

        if not entry_grid:
            logger.warning(f"⚠️ Сетап {signal['action']} [{self.symbol}] скасовано: недостатньо купівельної спроможності.")
            return

        order = entry_grid[0]
        amt = int(order.amount)
        if amt <= 0:
            return

        sl_p = round(float(signal['sl']), self.precision)
        tp_p = round(float(signal['tp']), self.precision)

        if not self.dry_run:
            try:
                # ФІК #5: attachAlgoOrds — TP/SL прикріплені до лімітки.
                # OKX активує їх автоматично в момент спрацювання лімітки,
                # тому захист гарантований навіть якщо бот впаде.
                self.client.create_order(
                    self.symbol, 'limit', entry_side, amt, order.price,
                    params={
                        'tdMode': 'isolated',
                        'posSide': pos_side,
                        'attachAlgoOrds': [{
                            'attachType': 'tp_sl',
                            'tpTriggerPx': str(tp_p),
                            'tpOrdPx':     '-1',
                            'slTriggerPx': str(sl_p),
                            'slOrdPx':     '-1',
                        }]
                    }
                )
                logger.info(
                    f"✅ Ордер виставлено з attached TP={tp_p} SL={sl_p} [{self.symbol}]. "
                    f"TP/SL активуються автоматично при спрацюванні лімітки."
                )
            except Exception as e:
                logger.error(f"Помилка виставлення лімітки з TP/SL [{self.symbol}]: {e}")
                # Fallback: гола лімітка — TP/SL виставимо через _set_tp_sl_immediately
                # коли побачимо відкриту позицію в execute()
                try:
                    self.client.create_order(
                        self.symbol, 'limit', entry_side, amt, order.price,
                        params={'tdMode': 'isolated', 'posSide': pos_side}
                    )
                    logger.warning(
                        f"⚠️ Fallback: лімітка без TP/SL [{self.symbol}]. "
                        f"Захист виставиться ОДРАЗУ після відкриття позиції."
                    )
                except Exception as e2:
                    logger.error(f"Fallback також не вдався [{self.symbol}]: {e2}")
                    return

        avg_entry = order.price
        margin = (amt * avg_entry * self.multiplier) / self.leverage

        self.is_be_set = False
        self.pending_trade = {
            'action': signal['action'], 'avg_entry': avg_entry,
            'contracts': amt, 'margin': margin,
            'tp': tp_p, 'sl': sl_p, 'limits_count': 1
        }

        details_text = (
            f"📝 <b>ДЕТАЛІ ПОЗИЦІЇ (Один вхід)</b>\n━━━━━━━━━━━━━━━\n"
            f"🧭 <b>Напрямок:</b> {'📈' if is_long else '📉'} <b>{signal['action']} {self.symbol}</b>\n"
            f"🎯 <b>Вхід:</b> {avg_entry} | {amt} контр.\n"
            f"✅ Take Profit: {tp_p}\n"
            f"🛑 Stop Loss: {sl_p}\n"
            f"━━━━━━━━━━━━━━━\n💰 Маржа: <b>~${margin:.2f}</b>\n⚖️ R:R = 1:{self.rr}"
        )

        trade_id = str(uuid.uuid4())[:8]
        TRADE_DETAILS_CACHE[trade_id] = details_text
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📊 Детальна інформація", callback_data=f"info|{trade_id}"))

        notifier.send_message(
            f"{'📈' if is_long else '📉'} <b>[СЕТАП] {signal['action']} {self.symbol} ({self.timeframe})</b>\n"
            f"Снайперський ордер виставлено! TP/SL прикріплені.",
            reply_markup=markup
        )