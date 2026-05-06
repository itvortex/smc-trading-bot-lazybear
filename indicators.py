import pandas as pd
import pandas_ta_classic as ta
import logging

logger = logging.getLogger("Bot-Core")

MIN_CANDLES_EMA200 = 200
MIN_CANDLES_BASIC = 20  # Мінімум для RSI та інших індикаторів


def find_smc_indicators(df: pd.DataFrame, timeframe: str = 'unknown') -> pd.DataFrame:
    if df is None or df.empty:
        return df

    df = df.copy()
    n = len(df)
    tf = timeframe  # наприклад '5m', '1h'

    # EMA-200
    if n >= MIN_CANDLES_EMA200:
        ema = ta.ema(df['close'], length=200)
        df['ema_200'] = ema if ema is not None else float('nan')
    else:
        df['ema_200'] = float('nan')

    # RSI — ключ з таймфреймом для ML
    if n >= MIN_CANDLES_BASIC:
        rsi = ta.rsi(df['close'], length=14)
        df[f'rsi_{tf}'] = rsi if rsi is not None else float('nan')
        df['rsi'] = df[f'rsi_{tf}']  # сумісність зі старим кодом
    else:
        df[f'rsi_{tf}'] = float('nan')
        df['rsi'] = float('nan')

    # ATR — для ML
    if n >= MIN_CANDLES_BASIC:
        atr = ta.atr(df['high'], df['low'], df['close'], length=14)
        df[f'atr_{tf}'] = atr if atr is not None else float('nan')
    else:
        df[f'atr_{tf}'] = float('nan')

    # ADX — для ML
    if n >= MIN_CANDLES_BASIC:
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_df is not None and f'ADX_14' in adx_df.columns:
            df[f'adx_{tf}'] = adx_df['ADX_14']
        else:
            df[f'adx_{tf}'] = float('nan')
    else:
        df[f'adx_{tf}'] = float('nan')

    # FVG і OB — без змін
    df['fvg_bullish'] = df['low'] > df['high'].shift(2)
    df['fvg_bearish'] = df['high'] < df['low'].shift(2)
    df['bullish_ob'] = (
        (df['close'] > df['high'].shift(1)) &
        (df['close'].shift(1) < df['open'].shift(1)) &
        (df['volume'] > df['volume'].shift(1))
    )
    df['bearish_ob'] = (
        (df['close'] < df['low'].shift(1)) &
        (df['close'].shift(1) > df['open'].shift(1)) &
        (df['volume'] > df['volume'].shift(1))
    )

    return df

# Сумісність зі старим кодом
find_order_blocks = find_smc_indicators