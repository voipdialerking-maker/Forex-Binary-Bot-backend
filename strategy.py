import pandas as pd
import numpy as np
import logging
import config as config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Strategy")

def check_strategy_signal(df: pd.DataFrame) -> dict:
    """
    Checks the last completed 5m candle (index -2) for CALL/PUT signals.
    Returns a dictionary with potential signal details, or None if no setup.
    """
    if len(df) < 30:
        return None

    completed_candle = df.iloc[-2]
    
    close = completed_candle['close']
    bb_upper = completed_candle['bb_upper']
    bb_lower = completed_candle['bb_lower']
    rsi = completed_candle['rsi']
    stoch_k = completed_candle['stoch_k']
    volume_ratio = completed_candle['volume_ratio']
    volume = completed_candle['volume']

    signal = None
    
    # Determine if volume data is active (if it's fallback 1.0, standard deviation will be 0)
    has_volume = (df['volume'].std() > 0) if len(df) > 0 else False
    volume_condition = (volume_ratio >= config.VOLUME_CLIMAX_MULTIPLIER) if has_volume else True
    
    # Check potential CALL Condition
    if (close < bb_lower) and (stoch_k < config.STOCH_OVERSOLD) and (rsi < config.RSI_OVERSOLD) and volume_condition:
        signal = "CALL"

    # Check potential PUT Condition
    elif (close > bb_upper) and (stoch_k > config.STOCH_OVERBOUGHT) and (rsi > config.RSI_OVERBOUGHT) and volume_condition:
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

def validate_1m_exhaustion(candles_1m: list, direction: str) -> bool:
    """
    Validates a potential 5m signal using 1m candle price action and wicks (Concept 2).
    candles_1m: A list of 5 dictionaries containing 1m candles: [{'open', 'high', 'low', 'close', 'volume'}, ...]
    direction: 'CALL' or 'PUT'
    
    Returns True if exhaustion and wick checks pass, False otherwise.
    """
    if len(candles_1m) < 5:
        logger.warning(f"Validation failed: Received only {len(candles_1m)} 1m candles (need 5).")
        return False

    # Extract candle parameters (most recent candle is index -1)
    bodies = []
    volumes = []
    
    for c in candles_1m:
        o = float(c['open'])
        h = float(c['high'])
        l = float(c['low'])
        cl = float(c['close'])
        vol = float(c.get('volume', 1.0))
        
        bodies.append(abs(cl - o))
        volumes.append(vol)

    # 1m Candle index mappings:
    # 0, 1, 2 = first three candles of the 5m interval
    # 3 = fourth candle
    # 4 = fifth (most recent) candle
    
    body_5 = bodies[4]
    vol_5 = volumes[4]

    # Calculate average body size of first 3 candles to verify momentum decay
    body_avg_123 = sum(bodies[0:3]) / 3.0
    
    # ----------------------------------------------------
    # Check A: Size Decay (Momentum Exhaustion)
    # The 5th candle body should be smaller than average body size of first 3
    # ----------------------------------------------------
    if body_avg_123 > 0:
        size_decay_passed = body_5 <= (body_avg_123 * 0.85) # 15%+ reduction in size
    else:
        size_decay_passed = True
        
    # **Breakout Safety Filter:**
    # If the 5th candle is a massive breakout candle, reject the trade
    avg_prev_bodies = sum(bodies[0:4]) / 4.0
    is_breakout = body_5 > (avg_prev_bodies * 2.2) if avg_prev_bodies > 0 else False
    
    if is_breakout:
        logger.info(f"1m Validation REJECTED: 5th 1m candle is a strong breakout push.")
        return False
        
    if not size_decay_passed:
        logger.info(f"1m Validation REJECTED: No size decay on 1m chart (Body 5: {body_5:.6f}, Avg 1-3: {body_avg_123:.6f}).")
        return False

    # ----------------------------------------------------
    # Check B: Volume Decay (Energy Exhaustion)
    # ----------------------------------------------------
    # Check if we have active volume variations
    vol_std = np.std(volumes)
    if vol_std > 0.01:
        vol_avg_123 = sum(volumes[0:3]) / 3.0
        vol_decay_passed = vol_5 < vol_avg_123
        if not vol_decay_passed:
            logger.info(f"1m Validation REJECTED: No volume decay on 1m chart (Vol 5: {vol_5}, Avg 1-3: {vol_avg_123:.2f}).")
            return False
    else:
        # Volume not active / uniform fallback, skip
        pass

    # ----------------------------------------------------
    # Check C: Rejection Wick (Pinbar shadow)
    # ----------------------------------------------------
    last_candle = candles_1m[-1]
    o_5 = float(last_candle['open'])
    h_5 = float(last_candle['high'])
    l_5 = float(last_candle['low'])
    cl_5 = float(last_candle['close'])
    candle_range = h_5 - l_5
    
    if candle_range <= 0:
        logger.warning("1m Validation: Candle range is zero. Rejecting.")
        return False

    if direction == "CALL":
        # We need a bottom wick showing rejection of lower prices
        lower_shadow = min(o_5, cl_5) - l_5
        wick_ratio = lower_shadow / candle_range
        wick_passed = wick_ratio >= 0.25  # Lower shadow must be at least 25% of candle range
        if not wick_passed:
            logger.info(f"1m Validation REJECTED: Lower shadow ratio ({wick_ratio:.2f}) < 0.25 (need bottom rejection wick).")
            return False
            
    elif direction == "PUT":
        # We need a top wick showing rejection of higher prices
        upper_shadow = h_5 - max(o_5, cl_5)
        wick_ratio = upper_shadow / candle_range
        wick_passed = wick_ratio >= 0.25  # Upper shadow must be at least 25% of candle range
        if not wick_passed:
            logger.info(f"1m Validation REJECTED: Upper shadow ratio ({wick_ratio:.2f}) < 0.25 (need top rejection wick).")
            return False

    logger.info(f"1m Validation APPROVED! Wick Ratio: {wick_ratio:.2f}, Size Decay Passed.")
    return True
