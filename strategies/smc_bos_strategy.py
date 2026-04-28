"""
SMC BOS/CHOCH Strategy — повноцінна Smart Money стратегія.
"""

import logging
import uuid
from typing import Optional, Dict, Any, List

import pandas as pd
import numpy as np
from telebot import types

from .base_strategy import BaseStrategy
from risk_manager import RiskManager
from notifier import notifier, TRADE_DETAILS_CACHE

logger = logging.getLogger(__name__)

FRACTAL_BARS = 2


# ══════════════════════════════════════════════════════
#  ДОПОМІЖНІ ФУНКЦІЇ АНАЛІЗУ
# ══════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, bars: int = FRACTAL_BARS):
    highs = df['high'].values
    lows  = df['low'].values
    n     = len(df)

    swing_highs = np.full(n, np.nan)
    swing_lows  = np.full(n, np.nan)

    for i in range(bars, n - bars):
        if all(highs[i] > highs[i - j] for j in range(1, bars + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, bars + 1)):
            swing_highs[i] = highs[i]

        if all(lows[i] < lows[i - j] for j in range(1, bars + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, bars + 1)):
            swing_lows[i] = lows[i]

    return pd.Series(swing_highs, index=df.index), pd.Series(swing_lows, index=df.index)


def find_bos_choch(df: pd.DataFrame, swing_highs: pd.Series, swing_lows: pd.Series):
    sh_indices = swing_highs.dropna().index.tolist()
    sl_indices = swing_lows.dropna().index.tolist()

    if len(sh_indices) < 2 or len(sl_indices) < 2:
        return None

    closes = df['close']

    last_sh_idx  = sh_indices[-1]
    last_sh_val  = swing_highs[last_sh_idx]
    prev_sh_idx  = sh_indices[-2]
    prev_sh_val  = swing_highs[prev_sh_idx]

    last_sl_idx  = sl_indices[-1]
    last_sl_val  = swing_lows[last_sl_idx]
    prev_sl_idx  = sl_indices[-2]
    prev_sl_val  = swing_lows[prev_sl_idx]

    bullish_trend = last_sh_val > prev_sh_val and last_sl_val > prev_sl_val

    result = None

    recent_closes = closes.iloc[last_sh_idx:]
    bos_candle = recent_closes[recent_closes > last_sh_val]
    if not bos_candle.empty:
        bos_idx      = bos_candle.index[0]
        bos_candle_i = df.index.get_loc(bos_idx)
        result = {
            'type':       'BOS' if bullish_trend else 'CHOCH',
            'direction':  'BULLISH',
            'level':      last_sh_val,
            'index':      bos_candle_i,
            'choch_low':  float(df['low'].iloc[bos_candle_i]),
            'choch_high': None,
        }

    recent_closes_b = closes.iloc[last_sl_idx:]
    bos_candle_b    = recent_closes_b[recent_closes_b < last_sl_val]
    if not bos_candle_b.empty:
        bos_idx_b      = bos_candle_b.index[0]
        bos_candle_i_b = df.index.get_loc(bos_idx_b)
        bear_result = {
            'type':       'BOS' if not bullish_trend else 'CHOCH',
            'direction':  'BEARISH',
            'level':      last_sl_val,
            'index':      bos_candle_i_b,
            'choch_low':  None,
            'choch_high': float(df['high'].iloc[bos_candle_i_b]),
        }
        if result is None or bos_candle_i_b > result['index']:
            result = bear_result

    return result


def find_ob_by_bos(df: pd.DataFrame, bos: dict) -> Optional[dict]:
    bos_i     = bos['index']
    direction = bos['direction']
    lookback  = min(bos_i, 30)

    for i in range(bos_i - 1, bos_i - lookback, -1):
        candle = df.iloc[i]
        if direction == 'BULLISH' and candle['close'] < candle['open']:
            return {
                'high':  float(candle['high']),
                'low':   float(candle['low']),
                'index': i,
                'type':  'BULLISH'
            }
        elif direction == 'BEARISH' and candle['close'] > candle['open']:
            return {
                'high':  float(candle['high']),
                'low':   float(candle['low']),
                'index': i,
                'type':  'BEARISH'
            }
    return None


def find_fvg_in_zone(df: pd.DataFrame, direction: str,
                     zone_high: float, zone_low: float,
                     lookback: int = 50) -> Optional[dict]:
    recent = df.tail(lookback)

    for i in range(2, len(recent)):
        c     = recent.iloc[i]
        c_2   = recent.iloc[i - 2]
        price = (c['high'] + c['low']) / 2

        if direction == 'BULLISH':
            has_fvg = c['low'] > c_2['high']
            in_zone = zone_low <= price <= zone_high * 1.02
            if has_fvg and in_zone:
                return {'high': float(c['low']), 'low': float(c_2['high'])}

        elif direction == 'BEARISH':
            has_fvg = c['high'] < c_2['low']
            in_zone = zone_low * 0.98 <= price <= zone_high
            if has_fvg and in_zone:
                return {'high': float(c_2['low']), 'low': float(c['high'])}

    return None


def find_liquidity_pool(swing_highs: pd.Series, swing_lows: pd.Series,
                        direction: str, current_price: float) -> Optional[float]:
    if direction == 'BULLISH':
        candidates = swing_highs.dropna()
        above      = candidates[candidates > current_price]
        return float(above.iloc[0]) if not above.empty else None
    else:
        candidates = swing_lows.dropna()
        below      = candidates[candidates < current_price]
        return float(below.iloc[-1]) if not below.empty else None


# ══════════════════════════════════════════════════════
#  СТРАТЕГІЯ
# ══════════════════════════════════════════════════════

class SMCBOSStrategy(BaseStrategy):

    LTF_MAP = {
        '15m': '1m',
        '1h':  '5m',
        '4h':  '15m',
        '1d':  '1h',
    }

    def __init__(self, client, risk_manager: RiskManager, symbol: str,
                 timeframe: str = '15m', rr: float = 2.0,
                 leverage: int = 10, dry_run: bool = False):
        super().__init__(client, risk_manager, symbol, timeframe,
                         leverage=leverage, rr=rr, dry_run=dry_run)

    # ──────────────────────────────────────────
    # Аналіз
    # ──────────────────────────────────────────

    def analyze(self) -> Optional[Dict[str, Any]]:
        htf = self.TF_MAP.get(self.timeframe, '1h')
        df_htf = self.client.fetch_ohlcv(self.symbol, htf, limit=300)
        if df_htf.empty:
            return None

        ema_200    = df_htf['close'].ewm(span=200, adjust=False).mean().iloc[-1]
        curr_price = float(df_htf['close'].iloc[-1])
        htf_bias   = 'BULLISH' if curr_price > ema_200 else 'BEARISH'

        logger.debug(f"[{self.symbol}] HTF bias: {htf_bias} | Price: {curr_price:.4f} | EMA200: {ema_200:.4f}")

        sh_htf, sl_htf = find_swing_points(df_htf)
        bos_htf        = find_bos_choch(df_htf, sh_htf, sl_htf)

        if bos_htf is None:
            logger.debug(f"[{self.symbol}] HTF BOS не знайдено")
            return None

        if bos_htf['direction'] != htf_bias and bos_htf['type'] != 'CHOCH':
            logger.debug(f"[{self.symbol}] BOS {bos_htf['direction']} не відповідає bias {htf_bias}")
            return None

        ob_htf = find_ob_by_bos(df_htf, bos_htf)
        if ob_htf is None:
            logger.debug(f"[{self.symbol}] HTF OB не знайдено")
            return None

        ob_high = ob_htf['high']
        ob_low  = ob_htf['low']

        tolerance   = (ob_high - ob_low) * 0.5
        in_ob_zone  = (ob_low - tolerance) <= curr_price <= (ob_high + tolerance)

        if not in_ob_zone:
            logger.debug(
                f"[{self.symbol}] Ціна {curr_price:.4f} поза зоною HTF OB "
                f"[{ob_low:.4f} — {ob_high:.4f}]"
            )
            return None

        logger.info(f"[{self.symbol}] ✅ Ціна в зоні HTF OB [{ob_low:.4f} — {ob_high:.4f}]")

        ltf    = self.LTF_MAP.get(htf, '5m')
        df_ltf = self.client.fetch_ohlcv(self.symbol, ltf, limit=200)
        if df_ltf.empty:
            return None

        sh_ltf, sl_ltf = find_swing_points(df_ltf, bars=2)
        bos_ltf        = find_bos_choch(df_ltf, sh_ltf, sl_ltf)

        if bos_ltf is None or bos_ltf['type'] != 'CHOCH':
            logger.debug(f"[{self.symbol}] LTF CHOCH не знайдено")
            return None

        if bos_ltf['direction'] != htf_bias:
            logger.debug(
                f"[{self.symbol}] LTF CHOCH {bos_ltf['direction']} "
                f"не відповідає HTF {htf_bias}"
            )
            return None

        logger.info(f"[{self.symbol}] ✅ LTF CHOCH підтверджено: {bos_ltf['direction']}")

        fvg = find_fvg_in_zone(df_ltf, htf_bias, ob_high, ob_low, lookback=50)

        if fvg is None:
            fvg = {'high': ob_high, 'low': ob_low}
            logger.info(f"[{self.symbol}] FVG не знайдено, вхід на середину OB")
        else:
            logger.info(f"[{self.symbol}] ✅ FVG знайдено: [{fvg['low']:.4f} — {fvg['high']:.4f}]")

        return self._prepare_signal(
            action   = 'LONG' if htf_bias == 'BULLISH' else 'SHORT',
            fvg      = fvg,
            ob       = ob_htf,
            bos_ltf  = bos_ltf,
            sh       = sh_htf,
            sl       = sl_htf,
            curr_price = curr_price,
        )

    def _prepare_signal(self, action: str, fvg: dict, ob: dict,
                        bos_ltf: dict, sh: pd.Series, sl: pd.Series,
                        curr_price: float) -> Optional[Dict[str, Any]]:
        """
        Формує параметри угоди і валідує що TP/SL коректні.

        ФІК #2: Перевіряємо що TP вище entry для LONG і нижче для SHORT.
        Якщо розрахунок дав некоректний результат — повертаємо None (сетап пропускається).
        """
        if action == 'LONG':
            entry = fvg['low']
            sl_price = bos_ltf['choch_low'] * 0.999 if bos_ltf.get('choch_low') else ob['low'] * 0.998

            liq_pool = find_liquidity_pool(sh, sl, 'BULLISH', curr_price)
            if liq_pool and (liq_pool - entry) / max(entry - sl_price, 1e-10) >= self.rr:
                tp_price = liq_pool
            else:
                tp_price = entry + (entry - sl_price) * self.rr

            # ФІК #2: валідація для LONG — TP має бути ВИЩЕ entry, SL НИЖЧЕ
            if tp_price <= entry:
                logger.warning(
                    f"[{self.symbol}] ❌ Некоректний LONG сетап: TP ({tp_price:.5f}) <= entry ({entry:.5f}). "
                    f"Сетап пропускається."
                )
                return None
            if sl_price >= entry:
                logger.warning(
                    f"[{self.symbol}] ❌ Некоректний LONG сетап: SL ({sl_price:.5f}) >= entry ({entry:.5f}). "
                    f"Сетап пропускається."
                )
                return None

        else:  # SHORT
            entry = fvg['high']
            sl_price = bos_ltf['choch_high'] * 1.001 if bos_ltf.get('choch_high') else ob['high'] * 1.002

            liq_pool = find_liquidity_pool(sh, sl, 'BEARISH', curr_price)
            if liq_pool and (entry - liq_pool) / max(sl_price - entry, 1e-10) >= self.rr:
                tp_price = liq_pool
            else:
                tp_price = entry - (sl_price - entry) * self.rr

            # ФІК #2: валідація для SHORT — TP має бути НИЖЧЕ entry, SL ВИЩЕ
            if tp_price >= entry:
                logger.warning(
                    f"[{self.symbol}] ❌ Некоректний SHORT сетап: TP ({tp_price:.5f}) >= entry ({entry:.5f}). "
                    f"Сетап пропускається."
                )
                return None
            if sl_price <= entry:
                logger.warning(
                    f"[{self.symbol}] ❌ Некоректний SHORT сетап: SL ({sl_price:.5f}) <= entry ({entry:.5f}). "
                    f"Сетап пропускається."
                )
                return None

        # ФІК #2: фінальна перевірка RR (мінімум 1:1)
        risk = abs(entry - sl_price)
        reward = abs(tp_price - entry)
        actual_rr = reward / risk if risk > 0 else 0
        if actual_rr < 1.0:
            logger.warning(
                f"[{self.symbol}] ❌ RR занадто низький: 1:{actual_rr:.2f} "
                f"(entry={entry:.5f}, sl={sl_price:.5f}, tp={tp_price:.5f}). Пропускаємо."
            )
            return None

        return {
            'action':    action,
            'entry':     round(entry,    self.precision),
            'sl':        round(sl_price, self.precision),
            'tp':        round(tp_price, self.precision),
            'ob_high':   ob['high'],
            'ob_low':    ob['low'],
            'fvg_high':  fvg['high'],
            'fvg_low':   fvg['low'],
        }

    # ──────────────────────────────────────────
    # Головний цикл (execute)
    # ──────────────────────────────────────────

    def execute(self) -> None:
        try:
            positions  = self.client.exchange.fetch_positions([self.symbol])
            pos        = next((p for p in positions if float(p.get('contracts', 0)) > 0), None)
            open_orders = self.client.exchange.fetch_open_orders(self.symbol)
        except Exception as e:
            logger.error(f"Execution API Error [{self.symbol}]: {e}")
            return

        if pos:
            if not self.is_active_position:
                self.is_active_position = True
                algo_orders = []
                try:
                    algo_orders = self.client.exchange.fetch_open_orders(
                        self.symbol, params={'stop': True}
                    )
                except Exception:
                    pass

                if not algo_orders:
                    logger.warning(f"⚠️ TP/SL не знайдено після входу [{self.symbol}] — виставляємо вручну.")
                    self._update_sl_tp(pos)
                    notifier.send_message(
                        f"🔔 <b>Позицію відкрито [{self.symbol}]</b>\n"
                        f"Захист виставлено вручну (fallback)."
                    )
                else:
                    notifier.send_message(
                        f"🔔 <b>Позицію відкрито [{self.symbol}]</b>\n"
                        f"TP/SL активні."
                    )
            self._manage_breakeven(pos)
            return

        if self.is_active_position and not pos:
            self.is_active_position = False
            self.is_be_set          = False
            try:
                for order in open_orders:
                    self.client.exchange.cancel_order(order['id'], self.symbol)
            except Exception as e:
                logger.error(f"Помилка скасування ордерів [{self.symbol}]: {e}")
            self._cancel_all_algo_orders()
            self._handle_trade_closure()
            return

        if not pos and len(open_orders) > 0:
            if self._check_tp_invalidation(open_orders):
                return

        if not pos and len(open_orders) == 0:
            self.pending_trade = None
            self._execute_new_trade()

    def _update_sl_tp(self, pos):
        """Fallback: виставляє SL/TP вручну якщо attachAlgoOrds не спрацював."""
        if not self.pending_trade or self.dry_run:
            return
        try:
            contracts  = float(pos['contracts'])
            action     = 'LONG' if str(pos['side']).upper() == 'LONG' else 'SHORT'
            close_side = 'sell' if action == 'LONG' else 'buy'
            pos_side   = 'long' if action == 'LONG' else 'short'
            tp_p = self.pending_trade['tp']
            sl_p = self.pending_trade['sl']

            self.client.create_order(
                self.symbol, 'market', close_side, contracts,
                params={'triggerPrice': tp_p, 'reduceOnly': True,
                        'tdMode': 'isolated', 'posSide': pos_side}
            )
            self.client.create_order(
                self.symbol, 'market', close_side, contracts,
                params={'triggerPrice': sl_p, 'reduceOnly': True,
                        'tdMode': 'isolated', 'posSide': pos_side}
            )
            logger.info(f"🔄 Fallback SL/TP виставлено [{self.symbol}]. TP: {tp_p}, SL: {sl_p}")
        except Exception as e:
            logger.error(f"Помилка fallback SL/TP [{self.symbol}]: {e}")

    # ──────────────────────────────────────────
    # Розміщення нового ордера
    # ──────────────────────────────────────────

    def _execute_new_trade(self) -> None:
        signal = self.analyze()
        if not signal:
            return

        is_long   = signal['action'] == 'LONG'
        pos_side  = 'long'  if is_long else 'short'
        entry_side = 'buy'  if is_long else 'sell'

        if not self.dry_run:
            try:
                self.client.set_leverage(
                    self.leverage, self.symbol,
                    margin_mode='isolated', pos_side=pos_side
                )
            except Exception as e:
                logger.error(f"Помилка встановлення плеча [{self.symbol}]: {e}")

        entry_grid = self.risk_manager.calculate_single_order(
            self.symbol,
            signal['entry'],
            signal['entry']
        )
        if not entry_grid:
            logger.warning(f"⚠️ [{self.symbol}] Недостатньо капіталу для угоди.")
            return

        order = entry_grid[0]
        amt   = int(order.amount)
        if amt <= 0:
            return

        tp_p = signal['tp']
        sl_p = signal['sl']

        if not self.dry_run:
            try:
                self.client.create_order(
                    self.symbol, 'limit', entry_side, amt, signal['entry'],
                    params={
                        'tdMode':   'isolated',
                        'posSide':  pos_side,
                        'attachAlgoOrds': [{
                            'attachType':  'tp_sl',
                            'tpTriggerPx': str(tp_p),
                            'tpOrdPx':     '-1',
                            'slTriggerPx': str(sl_p),
                            'slOrdPx':     '-1',
                        }]
                    }
                )
                logger.info(
                    f"✅ [{self.symbol}] Ордер виставлено: {signal['action']} "
                    f"вхід={signal['entry']} TP={tp_p} SL={sl_p}"
                )
            except Exception as e:
                logger.error(f"Помилка виставлення ордера [{self.symbol}]: {e}")
                try:
                    self.client.create_order(
                        self.symbol, 'limit', entry_side, amt, signal['entry'],
                        params={'tdMode': 'isolated', 'posSide': pos_side}
                    )
                    logger.warning(f"⚠️ [{self.symbol}] Fallback: лімітка без TP/SL.")
                except Exception as e2:
                    logger.error(f"Fallback також не вдався [{self.symbol}]: {e2}")
                    return

        margin = (amt * signal['entry'] * self.multiplier) / self.leverage

        self.is_be_set = False
        self.pending_trade = {
            'action':    signal['action'],
            'avg_entry': signal['entry'],
            'contracts': amt,
            'margin':    margin,
            'tp':        tp_p,
            'sl':        sl_p,
        }

        rr_actual = round(abs(tp_p - signal['entry']) / abs(signal['entry'] - sl_p), 2)

        details_text = (
            f"📝 <b>ДЕТАЛІ ПОЗИЦІЇ (BOS/CHOCH)</b>\n━━━━━━━━━━━━━━━\n"
            f"🧭 <b>Напрямок:</b> {'📈' if is_long else '📉'} <b>{signal['action']} {self.symbol}</b>\n"
            f"🎯 <b>Вхід (FVG межа):</b> <code>{signal['entry']}</code>\n"
            f"✅ <b>Take Profit:</b> <code>{tp_p}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>{sl_p}</code>\n"
            f"📦 <b>HTF OB зона:</b> <code>{signal['ob_low']:.4f} — {signal['ob_high']:.4f}</code>\n"
            f"⚡ <b>FVG зона:</b> <code>{signal['fvg_low']:.4f} — {signal['fvg_high']:.4f}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Маржа: <b>~${margin:.2f}</b> | R:R = 1:<b>{rr_actual}</b>\n"
            f"⚖️ Плече: <b>{self.leverage}x</b>"
        )

        trade_id = str(uuid.uuid4())[:8]
        TRADE_DETAILS_CACHE[trade_id] = details_text
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📊 Деталі сетапу", callback_data=f"info|{trade_id}"))

        notifier.send_message(
            f"{'📈' if is_long else '📉'} <b>[BOS СЕТАП] {signal['action']} {self.symbol}</b>\n"
            f"HTF OB + LTF CHOCH + FVG підтверджено. Ордер виставлено.",
            reply_markup=markup
        )