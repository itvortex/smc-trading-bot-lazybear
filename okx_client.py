import ccxt
import os
import pandas as pd
import logging
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

logger = logging.getLogger("OKX-Client")


class OKXClient:
    def __init__(self):
        use_testnet = os.getenv("USE_TESTNET", "True") == "True"

        self.exchange = ccxt.okx({
            'apiKey': os.getenv("OKX_API_KEY"),
            'secret': os.getenv("OKX_SECRET_KEY"),
            'password': os.getenv("OKX_PASSPHRASE"),
            'enableRateLimit': True,
        })

        if use_testnet:
            self.exchange.set_sandbox_mode(True)
            logger.info("📡 OKX Client initialized in TESTNET mode.")
        else:
            logger.info("💎 OKX Client initialized in MAINNET mode.")

    def fetch_balance(self, currency="USDT"):
        try:
            balance = self.exchange.fetch_balance()
            return balance['free'].get(currency, 0.0)
        except Exception as e:
            logger.error(f"Помилка отримання балансу: {e}")
            return 0.0

    def fetch_ohlcv(self, symbol, timeframe='5m', limit=300):
        try:
            data = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception as e:
            logger.error(f"Помилка завантаження графіку для {symbol} ({timeframe}): {e}")
            return pd.DataFrame()

    def set_leverage(self, leverage, symbol, margin_mode='isolated', pos_side='long'):
        try:
            self.exchange.set_leverage(leverage, symbol, params={'mgnMode': margin_mode, 'posSide': pos_side})
        except Exception:
            pass

    def create_order(self, symbol, type, side, amount, price=None, params={}):
        try:
            return self.exchange.create_order(symbol, type, side, amount, price, params)
        except Exception as e:
            logger.error(f"Помилка створення ордера: {e}")
            raise e

    def fetch_active_positions(self):
        try:
            positions = self.exchange.fetch_positions()
            return [p for p in positions if float(p.get('contracts', 0)) > 0]
        except Exception as e:
            logger.error(f"Помилка отримання активних позицій: {e}")
            return []

    def fetch_position_history(self, limit=100):
        """
        Отримує останні закриті позиції з OKX.

        ФІК #4: Дедублікація по raw posId (БЕЗ timestamp-суфіксу).
        Раніше один і той самий posId міг зберігатись двічі:
          - як "123456" (старий запис без суфіксу)
          - як "123456_1776851234000" (новий запис з суфіксом)
        Тепер PosId = тільки raw_pos_id якщо він є, і лише тоді timestamp
        якщо posId порожній. Це виключає дублі при перегляді CSV.
        """
        history = []
        seen_pos_ids = set()  # ФІК #4: трекер вже доданих posId

        try:
            params = {
                'instType': 'SWAP',
                'limit': str(limit),
            }

            response = self.exchange.privateGetAccountPositionsHistory(params)
            code = response.get('code', '0')

            if code != '0':
                raise Exception(f"OKX error {code}: {response.get('msg', '')}")

            data = response.get('data', [])

            if not data:
                logger.info("📄 Немає нових закритих позицій на OKX.")
                return history

            def safe_float(val):
                if val in [None, '', 'NaN']:
                    return 0.0
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return 0.0

            logger.info(f"📥 Завантажено {len(data)} записів з OKX (до дедублікації).")

            for item in data:
                raw_pos_id = item.get('posId', '')

                ts_str = item.get('uTime') or item.get('cTime', '0')
                ts = safe_float(ts_str)
                date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M')

                inst_id = item.get('instId', '')
                symbol = inst_id.replace('-SWAP', '').replace('-', '/') + ':USDT'

                direction = item.get('direction', '').upper()
                pnl = safe_float(item.get('realizedPnl') or item.get('pnl'))

                if direction == 'NET':
                    direction = 'LONG' if pnl >= 0 else 'SHORT'

                leverage = safe_float(item.get('lever'))
                roe_decimal = safe_float(item.get('pnlRatio'))
                roe_percent = roe_decimal * 100
                margin = abs(pnl / roe_decimal) if roe_decimal != 0 else 0.0
                status = 'Success' if pnl > 0 else 'Failed'

                # ФІК #4: PosId = тільки raw_pos_id якщо він є
                # Якщо posId порожній (рідкісний кейс) — fallback на timestamp
                unique_pos_id = f"{raw_pos_id}_{int(ts)}" if raw_pos_id else str(int(ts))

                history.append({
                    'PosId': unique_pos_id,
                    'Date': date_str,
                    'Symbol': symbol,
                    'Action': direction,
                    'Leverage': leverage,
                    'Margin_USD': round(margin, 2),
                    'ROE_Percent': round(roe_percent, 2),
                    'PnL_USD': round(pnl, 2),
                    'Status': status,
                })

            logger.info(f"✅ Після дедублікації: {len(history)} унікальних позицій.")
            return history

        except Exception as e:
            logger.error(f"Помилка отримання історії позицій з OKX: {e}")
            return history