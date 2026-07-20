import pandas as pd
import numpy as np
import logging
import config as config
from datetime import datetime, timezone
from indicators import calculate_ema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Strategy")

def is_valid_trading_session(epoch: int) -> bool:
    """
    Checks if the given epoch falls within the active trading session (e.g. 07:00 to 17:00 UTC).
    """
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    hour = dt_utc.hour
    
    return config.SESSION_START_UTC <= hour < config.SESSION_END_UTC

def check_rsi_divergence(df: pd.DataFrame, direction: str, lookback: int = config.DIVERGENCE_LOOKBACK) -> bool:
    """
    Checks for Regular Bullish or Bearish RSI Divergence.
    """
    if len(df) < lookback + 2:
        return True # Not enough data to check, allow trade
        
    recent_df = df.iloc[-(lookback+2):-2]
    current_candle = df.iloc[-2]
    
    if direction == "CALL":
        min_close_idx = recent_df['close'].idxmin()
        prev_low_close = recent_df.loc[min_close_idx, 'close']
        prev_rsi = recent_df.loc[min_close_idx, 'rsi']
        
        # Price is lower or equal, but RSI is higher (momentum is building up)
        if current_candle['close'] <= prev_low_close and current_candle['rsi'] > prev_rsi:
            return True
            
    elif direction == "PUT":
        max_close_idx = recent_df['close'].idxmax()
        prev_high_close = recent_df.loc[max_close_idx, 'close']
        prev_rsi = recent_df.loc[max_close_idx, 'rsi']
        
        # Price is higher or equal, but RSI is lower (momentum is falling)
        if current_candle['close'] >= prev_high_close and current_candle['rsi'] < prev_rsi:
            return True
            
    return False

def check_strategy_signal(df: pd.DataFrame) -> dict:
    """
    Checks the last completed 5m candle (index -2) for CALL/PUT signals using BB, RSI, Stoch, MACD, and Divergence.
    """
    if len(df) < 30:
        return None

    completed_candle = df.iloc[-2]
    prev_candle = df.iloc[-3]
    
    close = completed_candle['close']
    bb_upper = completed_candle['bb_upper']
    bb_lower = completed_candle['bb_lower']
    rsi = completed_candle['rsi']
    stoch_k = completed_candle['stoch_k']
    volume_ratio = completed_candle['volume_ratio']
    volume = completed_candle['volume']
    macd_hist = completed_candle['macd_hist']
    prev_macd_hist = prev_candle['macd_hist']

    signal = None
    
    # Session Filter
    if not is_valid_trading_session(completed_candle['epoch']):
        return None
    
    has_volume = (df['volume'].std() > 0) if len(df) > 0 else False
    volume_condition = (volume_ratio >= config.VOLUME_CLIMAX_MULTIPLIER) if has_volume else True
    
    # MACD Momentum check
    macd_bullish = (macd_hist > prev_macd_hist) or (macd_hist > 0)
    macd_bearish = (macd_hist < prev_macd_hist) or (macd_hist < 0)
    
    # Check potential CALL Condition
    if (close < bb_lower) and (stoch_k < config.STOCH_OVERSOLD) and (rsi < config.RSI_OVERSOLD):
        if volume_condition and macd_bullish:
            signal = "CALL"

    # Check potential PUT Condition
    elif (close > bb_upper) and (stoch_k > config.STOCH_OVERBOUGHT) and (rsi > config.RSI_OVERBOUGHT):
        if volume_condition and macd_bearish:
            signal = "PUT"

    if signal:
        return {
            "signal": signal,
            "entry_price": close,
            "rsi": rsi,
            "stochastic": stoch_k,
            "volume_ratio": volume_ratio,
            "volume": volume,
            "epoch": completed_candle['epoch']
        }
    return None

def check_h1_trend(candles_h1: list, direction: str) -> bool:
    """
    Validates the H1 Trend using EMA 50 and EMA 200.
    """
    if len(candles_h1) < 200:
        logger.warning("Not enough H1 candles to calculate EMA 200.")
        return True # Allow if not enough data
        
    df = pd.DataFrame(candles_h1)
    df['close'] = pd.to_numeric(df['close'])
    
    df = calculate_ema(df, config.EMA_TREND_FAST)
    df = calculate_ema(df, config.EMA_TREND_SLOW)
    
    last_ema_50 = df[f'ema_{config.EMA_TREND_FAST}'].iloc[-1]
    last_ema_200 = df[f'ema_{config.EMA_TREND_SLOW}'].iloc[-1]
    
    if direction == "CALL":
        # Uptrend: EMA 50 > EMA 200
        is_uptrend = last_ema_50 > last_ema_200
        if not is_uptrend:
            logger.info("H1 Trend Validation REJECTED: Not in an uptrend (EMA 50 < EMA 200).")
        return is_uptrend
        
    elif direction == "PUT":
        # Downtrend: EMA 50 < EMA 200
        is_downtrend = last_ema_50 < last_ema_200
        if not is_downtrend:
            logger.info("H1 Trend Validation REJECTED: Not in a downtrend (EMA 50 > EMA 200).")
        return is_downtrend
        
    return False

def validate_1m_exhaustion(candles_1m: list, direction: str) -> bool:
    """
    Validates a potential 5m signal using 1m candlestick patterns and wicks.
    """
    if len(candles_1m) < 5:
        logger.warning(f"Validation failed: Received only {len(candles_1m)} 1m candles (need 5).")
        return False

    last_candle = candles_1m[-1]
    prev_candle = candles_1m[-2]
    
    o_5, cl_5 = float(last_candle['open']), float(last_candle['close'])
    h_5, l_5 = float(last_candle['high']), float(last_candle['low'])
    
    o_4, cl_4 = float(prev_candle['open']), float(prev_candle['close'])
    
    candle_range = h_5 - l_5
    body_5 = abs(cl_5 - o_5)
    
    if candle_range <= 0:
        return False
        
    # Candlestick Pattern Recognition
    is_doji = body_5 <= (candle_range * 0.1)
    
    is_bullish_engulfing = (cl_4 < o_4) and (cl_5 > o_5) and (cl_5 > o_4) and (o_5 < cl_4)
    is_bearish_engulfing = (cl_4 > o_4) and (cl_5 < o_5) and (cl_5 < o_4) and (o_5 > cl_4)
    
    lower_shadow = min(o_5, cl_5) - l_5
    upper_shadow = h_5 - max(o_5, cl_5)
    
    is_hammer = (lower_shadow >= 2 * body_5) and (upper_shadow <= 0.2 * candle_range)
    is_shooting_star = (upper_shadow >= 2 * body_5) and (lower_shadow <= 0.2 * candle_range)

    if direction == "CALL":
        # Require a strong reversal pattern
        if is_bullish_engulfing or is_hammer or is_doji or (lower_shadow / candle_range >= 0.3):
            logger.info("1m Validation APPROVED! Bullish pattern/rejection found.")
            return True
        else:
            logger.info("1m Validation REJECTED: No bullish pattern or rejection wick found.")
            return False
            
    elif direction == "PUT":
        # Require a strong reversal pattern
        if is_bearish_engulfing or is_shooting_star or is_doji or (upper_shadow / candle_range >= 0.3):
            logger.info("1m Validation APPROVED! Bearish pattern/rejection found.")
            return True
        else:
            logger.info("1m Validation REJECTED: No bearish pattern or rejection wick found.")
            return False

    return False
