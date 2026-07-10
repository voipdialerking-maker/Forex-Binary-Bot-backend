import pandas as pd
import numpy as np

def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, dev: float = 2.0) -> pd.DataFrame:
    """
    Calculates Upper, Middle, and Lower Bollinger Bands.
    """
    df = df.copy()
    df['bb_middle'] = df['close'].rolling(window=period).mean()
    df['bb_std'] = df['close'].rolling(window=period).std()
    df['bb_upper'] = df['bb_middle'] + (dev * df['bb_std'])
    df['bb_lower'] = df['bb_middle'] - (dev * df['bb_std'])
    return df

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Calculates Relative Strength Index (RSI).
    """
    df = df.copy()
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).copy()
    loss = (-delta.where(delta < 0, 0)).copy()

    # Wilder's smoothing
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    # Apply Wilder's smoothing technique
    for i in range(period, len(df)):
        avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

def calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """
    Calculates Stochastic Oscillator (%K and %D).
    """
    df = df.copy()
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()

    df['stoch_k'] = 100 * ((df['close'] - low_min) / (high_max - low_min))
    df['stoch_d'] = df['stoch_k'].rolling(window=d_period).mean()
    return df

def calculate_volume_metrics(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Calculates volume moving average and volume multiplier.
    """
    df = df.copy()
    # Deriv volume field is usually called 'volume' or 'count' (tick volume)
    # We ensure we have a numeric column
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
    
    df['volume_ma'] = df['volume'].rolling(window=period).mean()
    # Avoid division by zero
    df['volume_ratio'] = np.where(df['volume_ma'] > 0, df['volume'] / df['volume_ma'], 1.0)
    return df

def calculate_all_indicators(candles: list) -> pd.DataFrame:
    """
    Takes a list of candle dictionaries, parses it into a DataFrame,
    and appends all indicators.
    """
    df = pd.DataFrame(candles)
    # Ensure correct data types
    df['close'] = pd.to_numeric(df['close'])
    df['open'] = pd.to_numeric(df['open'])
    df['high'] = pd.to_numeric(df['high'])
    df['low'] = pd.to_numeric(df['low'])
    
    # Fallback if volume is missing (e.g. Forex pairs on Deriv)
    if 'volume' not in df.columns:
        df['volume'] = 1.0
    else:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(1.0)
    
    # Calculate indicators
    df = calculate_bollinger_bands(df)
    df = calculate_rsi(df)
    df = calculate_stochastic(df)
    df = calculate_volume_metrics(df)
    
    return df
