import pandas as pd
import pandas_ta_classic as ta
import logging

logger = logging.getLogger("Bot-Core")

MIN_CANDLES_EMA200 = 200
MIN_CANDLES_BASIC = 20  # Мінімум для RSI та інших індикаторів


def find_smc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Розраховує SMC індикатори: EMA-200, RSI, FVG, Order Blocks.

    Зміни порівняно з оригіналом:
    - Перевірка мінімальної кількості свічок перед розрахунком EMA-200
    - EMA заповнюється NaN якщо свічок недостатньо (замість краша)
    - Виправлена логіка FVG: правильні зсуви для bullish/bearish gap
    - Захист від None при розрахунку TA індикаторів
    """
    if df is None or df.empty:
        logger.warning("⚠️ find_smc_indicators: отримано порожній DataFrame")
        return df

    df = df.copy()
    n = len(df)

    # 1. EMA-200 — тільки якщо достатньо свічок
    if n >= MIN_CANDLES_EMA200:
        ema = ta.ema(df['close'], length=200)
        df['ema_200'] = ema if ema is not None else float('nan')
    else:
        logger.debug(f"⚠️ Замало свічок для EMA-200: {n} < {MIN_CANDLES_EMA200}. EMA=NaN.")
        df['ema_200'] = float('nan')

    # 2. RSI-14
    if n >= MIN_CANDLES_BASIC:
        rsi = ta.rsi(df['close'], length=14)
        df['rsi'] = rsi if rsi is not None else float('nan')
    else:
        df['rsi'] = float('nan')

    # 3. FAIR VALUE GAP (FVG) / IMBALANCE
    # Bullish FVG: між свічкою [i-2].high і свічкою [i].low є простір (gap вгору)
    # Тобто low поточної свічки > high свічки 2 назад
    df['fvg_bullish'] = df['low'] > df['high'].shift(2)

    # Bearish FVG: між свічкою [i-2].low і свічкою [i].high є простір (gap вниз)
    # Тобто high поточної свічки < low свічки 2 назад
    df['fvg_bearish'] = df['high'] < df['low'].shift(2)

    # 4. ORDER BLOCKS (OB)
    # Bullish OB: поточна свічка бичача і пробиває хай попередньої ведмежої свічки з підвищеним об'ємом
    df['bullish_ob'] = (
        (df['close'] > df['high'].shift(1)) &           # Пробиває хай попередньої
        (df['close'].shift(1) < df['open'].shift(1)) &  # Попередня — ведмежа
        (df['volume'] > df['volume'].shift(1))           # З підвищеним об'ємом
    )

    # Bearish OB: поточна свічка ведмежа і пробиває лоу попередньої бичачої свічки з підвищеним об'ємом
    df['bearish_ob'] = (
        (df['close'] < df['low'].shift(1)) &            # Пробиває лоу попередньої
        (df['close'].shift(1) > df['open'].shift(1)) &  # Попередня — бичача
        (df['volume'] > df['volume'].shift(1))           # З підвищеним об'ємом
    )

    return df


# Сумісність зі старим кодом
find_order_blocks = find_smc_indicators