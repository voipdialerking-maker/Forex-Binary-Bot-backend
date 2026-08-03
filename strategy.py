import pandas as pd
import numpy as np
import logging
import config as config
from datetime import datetime, timezone
from indicators import calculate_ema, calculate_sma

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

def check_trend_exhaustion(df: pd.DataFrame) -> dict:
    """
    Checks the last completed 5m candle (index -2) for CALL/PUT signals using BB, RSI, Stoch, and MACD.
    Strategy: Trend-Aligned Exhaustion.
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
    
    open_price = completed_candle['open']
    high_price = completed_candle['high']
    low_price = completed_candle['low']
    body = abs(close - open_price)
    lower_shadow = min(open_price, close) - low_price
    upper_shadow = high_price - max(open_price, close)
    
    # Check potential CALL Condition (swept BB lower with rejection wick + oversold extreme)
    if (low_price < bb_lower or close < bb_lower) and (stoch_k < config.STOCH_OVERSOLD) and (rsi < config.RSI_OVERSOLD):
        if volume_condition and lower_shadow >= body:
            signal = "CALL"

    # Check potential PUT Condition (swept BB upper with rejection wick + overbought extreme)
    elif (high_price > bb_upper or close > bb_upper) and (stoch_k > config.STOCH_OVERBOUGHT) and (rsi > config.RSI_OVERBOUGHT):
        if volume_condition and upper_shadow >= body:
            signal = "PUT"

    if signal:
        return {
            "signal": signal,
            "entry_price": close,
            "rsi": rsi,
            "stochastic": stoch_k,
            "volume_ratio": volume_ratio,
            "volume": volume,
            "epoch": completed_candle['epoch'],
            "strategy_name": "Trend Exhaustion"
        }
    return None

def check_smc_sweep(candles_m15: list, candles_1m: list) -> dict:
    """
    Checks for a Liquidity Sweep on the M15 timeframe and a rejection on the 1m timeframe.
    Strategy: SMC Sweep.
    """
    if len(candles_m15) < 20 or len(candles_1m) < 5:
        return None
        
    df_m15 = pd.DataFrame(candles_m15[-20:])
    df_m15['high'] = pd.to_numeric(df_m15['high'])
    df_m15['low'] = pd.to_numeric(df_m15['low'])
    
    # Exclude the currently forming M15 candle
    historical_m15 = df_m15.iloc[:-1]
    
    highest_high = historical_m15['high'].max()
    lowest_low = historical_m15['low'].min()
    
    completed_1m = candles_1m[-2]
    
    c_open = float(completed_1m['open'])
    c_close = float(completed_1m['close'])
    c_high = float(completed_1m['high'])
    c_low = float(completed_1m['low'])
    c_epoch = int(completed_1m['epoch'])
    vol_ratio = float(completed_1m.get('volume_ratio', 1.0))
    
    body = abs(c_close - c_open)
    lower_shadow = min(c_open, c_close) - c_low
    upper_shadow = c_high - max(c_open, c_close)
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    # Must have institutional Volume Spike (> 1.25x average) on the sweep candle
    if vol_ratio < 1.25:
        return None
        
    signal = None
    
    # CALL Setup: Swept the M15 Low, but closed back inside with strong rejection wick
    if c_low < lowest_low and c_close > lowest_low and lower_shadow >= (1.5 * body):
        if validate_1m_exhaustion(candles_1m, "CALL"):
            signal = "CALL"
            
    # PUT Setup: Swept the M15 High, but closed back inside with strong rejection wick
    elif c_high > highest_high and c_close < highest_high and upper_shadow >= (1.5 * body):
        if validate_1m_exhaustion(candles_1m, "PUT"):
            signal = "PUT"
            
    if signal:
        logger.info(f"SMC SWEEP DETECTED: {signal} at {c_close} (HH: {highest_high}, LL: {lowest_low})")
        return {
            "signal": signal,
            "entry_price": c_close,
            "rsi": None,
            "stochastic": None,
            "volume_ratio": None,
            "volume": float(completed_1m.get('volume', 1.0)),
            "epoch": c_epoch,
            "strategy_name": "SMC Sweep"
        }
    return None

def check_smc_structure_bias(df: pd.DataFrame, lookback: int = 25) -> str:
    """
    Pure Institutional SMC Structure & Displacement Bias Engine (No SMA/EMA/RSI indicators).
    1. Break of Structure (BOS) / Change of Character (CHoCH):
       - Analyzes recent Swing Highs and Swing Lows over lookback window.
       - Identifies if market structure is making Higher Highs / Higher Lows (BULLISH)
         or Lower Highs / Lower Lows (BEARISH).
    2. Fair Value Gap (FVG / Institutional Displacement):
       - Detects 3-candle institutional imbalances (unfilled price gaps).
       - Bullish FVG: low[k+2] > high[k] (buyers displaced aggressively).
       - Bearish FVG: high[k+2] < low[k] (sellers displaced aggressively).
    Returns 'BULLISH' only if Bullish BOS/Structure + Bullish FVG/Displacement are present.
    Returns 'BEARISH' only if Bearish BOS/Structure + Bearish FVG/Displacement are present.
    Returns 'NEUTRAL' otherwise (protects against sideways noise/fake breakouts).
    """
    if df is None or len(df) < lookback + 5:
        return "NEUTRAL"
        
    for col in ['open', 'high', 'low', 'close']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    win = df.iloc[-lookback:-1].copy().reset_index(drop=True)
    if len(win) < 15:
        return "NEUTRAL"
        
    # --- 1. IDENTIFY SWING HIGHS & SWING LOWS (Pivots lb=2, rb=2) ---
    swing_highs = []
    swing_lows = []
    for i in range(2, len(win) - 2):
        h = float(win['high'].iloc[i])
        l = float(win['low'].iloc[i])
        # Pivot High
        if h > win['high'].iloc[i-1] and h > win['high'].iloc[i-2] and h > win['high'].iloc[i+1] and h > win['high'].iloc[i+2]:
            swing_highs.append((i, h))
        # Pivot Low
        if l < win['low'].iloc[i-1] and l < win['low'].iloc[i-2] and l < win['low'].iloc[i+1] and l < win['low'].iloc[i+2]:
            swing_lows.append((i, l))
            
    last_close = float(win['close'].iloc[-1])
    last_open = float(win['open'].iloc[-1])
    
    # Structure State Determination
    struct_bullish = False
    struct_bearish = False
    
    if len(swing_highs) >= 1 and len(swing_lows) >= 1:
        latest_sh = swing_highs[-1][1]
        latest_sl = swing_lows[-1][1]
        
        # Check Break of Structure (BOS / CHoCH)
        if last_close > latest_sh:
            struct_bullish = True
        elif last_close < latest_sl:
            struct_bearish = True
        else:
            # Check structure trend progression
            if len(swing_highs) >= 2 and len(swing_lows) >= 2:
                if swing_highs[-1][1] >= swing_highs[-2][1] and swing_lows[-1][1] >= swing_lows[-2][1]:
                    struct_bullish = True
                elif swing_highs[-1][1] <= swing_highs[-2][1] and swing_lows[-1][1] <= swing_lows[-2][1]:
                    struct_bearish = True
    else:
        # Fallback to pure price displacement slope if no 5-bar pivot formed yet
        first_close = float(win['close'].iloc[0])
        if last_close > (first_close * 1.0005):
            struct_bullish = True
        elif last_close < (first_close * 0.9995):
            struct_bearish = True

    # --- 2. DETECT FAIR VALUE GAPS (FVG / INSTITUTIONAL DISPLACEMENT) ---
    has_bullish_fvg = False
    has_bearish_fvg = False
    
    # Check last 10 candles for unmitigated FVG or strong displacement body
    for k in range(max(0, len(win) - 10), len(win) - 2):
        c0_h = float(win['high'].iloc[k])
        c0_l = float(win['low'].iloc[k])
        c2_h = float(win['high'].iloc[k+2])
        c2_l = float(win['low'].iloc[k+2])
        
        # Bullish FVG: Gap between candle k High and candle k+2 Low
        if c2_l > (c0_h * 1.00005):
            has_bullish_fvg = True
        # Bearish FVG: Gap between candle k Low and candle k+2 High
        if c2_h < (c0_l * 0.99995):
            has_bearish_fvg = True
            
    # Also check if recent candles had institutional displacement body (> 1.5x average body)
    avg_body = (win['close'] - win['open']).abs().mean()
    if not has_bullish_fvg:
        has_bullish_fvg = any((win['close'].iloc[m] - win['open'].iloc[m]) >= (1.6 * avg_body) for m in range(max(0, len(win)-5), len(win)))
    if not has_bearish_fvg:
        has_bearish_fvg = any((win['open'].iloc[m] - win['close'].iloc[m]) >= (1.6 * avg_body) for m in range(max(0, len(win)-5), len(win)))

    if struct_bullish and has_bullish_fvg:
        return "BULLISH"
    elif struct_bearish and has_bearish_fvg:
        return "BEARISH"
    return "NEUTRAL"

def check_sma_smc_strategy(candles_m15: list, candles_1m: list) -> dict:
    """
    Evaluates Strategy 3: SMC Continuation (Pure BOS + FVG on M15 + M1 BOS and OB).
    1. M15 Pure Institutional BOS + FVG for Directional Bias.
    2. M1 BOS and OB Identification.
    3. M1 OB mitigation and rejection.
    """
    if len(candles_m15) < 30 or len(candles_1m) < 60:
        return None
        
    df_m15 = pd.DataFrame(candles_m15)
    bias_m15 = check_smc_structure_bias(df_m15, lookback=20)
    if bias_m15 == "NEUTRAL":
        return None
        
    direction = "CALL" if bias_m15 == "BULLISH" else "PUT"
    
    df_1m = pd.DataFrame(candles_1m)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df_1m[col] = pd.to_numeric(df_1m.get(col, 1.0))
        
    completed_1m = df_1m.iloc[-2]
    c_epoch = int(completed_1m['epoch'])
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    # Use all historical candles for a deeper structural search, excluding the last 2
    search_window = df_1m.iloc[:-2].reset_index(drop=True)
    if len(search_window) < 50:
        return None
        
    signal = None
    
    # We divide the history into two halves:
    # First half: to find the major Swing High / Low
    # Second half: to find the Break of Structure (BOS)
    midpoint = len(search_window) // 2
    
    if direction == "CALL":
        # 1. Find Swing High (Max high in the first half of the window)
        first_half = search_window.iloc[:midpoint]
        swing_high = first_half['high'].max()
        swing_high_idx = first_half['high'].idxmax()
        
        # 2. Find BOS (Candle that closed above swing_high in the second half)
        second_half = search_window.iloc[swing_high_idx+1:]
        bos_candles = second_half[second_half['close'] > swing_high]
        
        if not bos_candles.empty:
            bos_idx = bos_candles.index[0]
            
            # 3. Find Higher Low before BOS
            pullback_leg = search_window.iloc[swing_high_idx:bos_idx+1]
            higher_low = pullback_leg['low'].min()
            higher_low_idx = pullback_leg['low'].idxmin()
            
            # 4. Find OB (Last Red Candle at/before Higher Low)
            # Search backwards from higher_low_idx for a red candle
            ob_idx = -1
            for i in range(higher_low_idx, -1, -1):
                if search_window.iloc[i]['close'] < search_window.iloc[i]['open']:
                    ob_idx = i
                    break
                    
            if ob_idx != -1:
                ob_high = search_window.iloc[ob_idx]['high']
                ob_low = search_window.iloc[ob_idx]['low']
                
                # Check Invalidation: Any candle closed below OB low after BOS?
                invalid = False
                for i in range(bos_idx, len(search_window)):
                    if search_window.iloc[i]['close'] < ob_low:
                        invalid = True
                        break
                        
                if not invalid:
                    # Check Mitigation & Rejection on completed candle
                    c_low = completed_1m['low']
                    c_close = completed_1m['close']
                    
                    if c_low <= ob_high and c_close > ob_high:
                        # Rejection from OB
                        if validate_1m_exhaustion(candles_1m, "CALL"):
                            signal = "CALL"

    elif direction == "PUT":
        # 1. Find Swing Low (Min low in the first half of the window)
        first_half = search_window.iloc[:midpoint]
        swing_low = first_half['low'].min()
        swing_low_idx = first_half['low'].idxmin()
        
        # 2. Find BOS (Candle that closed below swing_low in the second half)
        second_half = search_window.iloc[swing_low_idx+1:]
        bos_candles = second_half[second_half['close'] < swing_low]
        
        if not bos_candles.empty:
            bos_idx = bos_candles.index[0]
            
            # 3. Find Lower High before BOS
            pullback_leg = search_window.iloc[swing_low_idx:bos_idx+1]
            lower_high = pullback_leg['high'].max()
            lower_high_idx = pullback_leg['high'].idxmax()
            
            # 4. Find OB (Last Green Candle at/before Lower High)
            ob_idx = -1
            for i in range(lower_high_idx, -1, -1):
                if search_window.iloc[i]['close'] > search_window.iloc[i]['open']:
                    ob_idx = i
                    break
                    
            if ob_idx != -1:
                ob_high = search_window.iloc[ob_idx]['high']
                ob_low = search_window.iloc[ob_idx]['low']
                
                # Check Invalidation: Any candle closed above OB high after BOS?
                invalid = False
                for i in range(bos_idx, len(search_window)):
                    if search_window.iloc[i]['close'] > ob_high:
                        invalid = True
                        break
                        
                if not invalid:
                    # Check Mitigation & Rejection on completed candle
                    c_high = completed_1m['high']
                    c_close = completed_1m['close']
                    
                    if c_high >= ob_low and c_close < ob_low:
                        # Rejection from OB
                        if validate_1m_exhaustion(candles_1m, "PUT"):
                            signal = "PUT"

    if signal:
        logger.info(f"SMA-SMC SIGNAL: {signal} @ {completed_1m['close']}")
        return {
            "signal": signal,
            "entry_price": float(completed_1m['close']),
            "rsi": None,
            "stochastic": None,
            "volume_ratio": None,
            "volume": float(completed_1m['volume']),
            "epoch": c_epoch,
            "strategy_name": "SMA-SMC Continuation"
        }
    return None

def check_m15_trend(candles_m15: list, direction: str) -> bool:
    """
    Validates the M15 Trend using EMA 50 and EMA 200.
    """
    if len(candles_m15) < 200:
        logger.warning("Not enough M15 candles to calculate EMA 200.")
        return True # Allow if not enough data
        
    df = pd.DataFrame(candles_m15)
    df['close'] = pd.to_numeric(df['close'])
    
    df = calculate_ema(df, config.EMA_TREND_FAST)
    df = calculate_ema(df, config.EMA_TREND_SLOW)
    
    last_ema_50 = df[f'ema_{config.EMA_TREND_FAST}'].iloc[-1]
    last_ema_200 = df[f'ema_{config.EMA_TREND_SLOW}'].iloc[-1]
    
    if direction == "CALL":
        # Uptrend: EMA 50 > EMA 200
        is_uptrend = last_ema_50 > last_ema_200
        if not is_uptrend:
            logger.info("M15 Trend Validation REJECTED: Not in an uptrend (EMA 50 < EMA 200).")
        return is_uptrend
        
    elif direction == "PUT":
        # Downtrend: EMA 50 < EMA 200
        is_downtrend = last_ema_50 < last_ema_200
        if not is_downtrend:
            logger.info("M15 Trend Validation REJECTED: Not in a downtrend (EMA 50 > EMA 200).")
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

def check_vsa_scalp_strategy(candles_m15: list, candles_1m: list) -> dict:
    """
    Evaluates Strategy 4: SMC Support/Resistance + VSA (Wick Rejection).
    DISABLED: Low win rate in 1m binary scalping.
    """
    return None
        
    import pandas as pd
    from indicators import calculate_sma, calculate_volume_metrics
    
    # --- 1. HIGHER TIMEFRAME (M15) ANALYSIS ---
    df_m15 = pd.DataFrame(candles_m15)
    for col in ['open', 'high', 'low', 'close']:
        df_m15[col] = pd.to_numeric(df_m15.get(col, 1.0))
        
    df_m15 = calculate_sma(df_m15, 9)
    df_m15 = calculate_sma(df_m15, 21)
    
    # Exclude the currently forming M15 candle for accurate SR marking
    historical_m15 = df_m15.iloc[:-1]
    last_m15 = historical_m15.iloc[-1]
    
    # M15 Trend
    m15_sma9 = last_m15['sma_9']
    m15_sma21 = last_m15['sma_21']
    m15_uptrend = m15_sma9 > m15_sma21
    m15_downtrend = m15_sma9 < m15_sma21
    
    # Mark Support & Resistance (recent 20 M15 candles)
    recent_m15 = historical_m15.iloc[-20:]
    m15_resistance = recent_m15['high'].max()
    m15_support = recent_m15['low'].min()
    
    # --- 2. LOWER TIMEFRAME (M1) ANALYSIS ---
    df_1m = pd.DataFrame(candles_1m)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df_1m[col] = pd.to_numeric(df_1m.get(col, 1.0))
        
    df_1m = calculate_volume_metrics(df_1m, 10) # 10-candle average for volume spike
    
    # We analyze the most recently COMPLETED 1m candle
    c = df_1m.iloc[-2]
    
    from strategy import is_valid_trading_session
    c_epoch = int(c['epoch'])
    if not is_valid_trading_session(c_epoch):
        return None
        
    open_p, close_p = float(c['open']), float(c['close'])
    high_p, low_p = float(c['high']), float(c['low'])
    vol_ratio = float(c.get('volume_ratio', 1.0))
    vol = float(c.get('volume', 1.0))
    
    spread = high_p - low_p
    body = abs(close_p - open_p)
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p
    
    if spread == 0:
        return None
        
    # Calculate M1 Average Spread for proximity threshold
    avg_spread = (df_1m['high'].iloc[-22:-2] - df_1m['low'].iloc[-22:-2]).mean()
    proximity_threshold = avg_spread * 1.5 # Must be within 1.5x of an average M1 candle to the M15 level
    
    signal = None
    vsa_type = ""
    
    # Volume Spike Requirement: Must be > 1.25x the 10-period average
    has_volume_spike = vol_ratio > 1.25
    
    # SCENARIO A: BUY (CALL) Setup at Support
    if m15_uptrend:
        # Check if price is near M15 Support
        if low_p <= (m15_support + proximity_threshold):
            # Check Wick Rejection (Hammer-like)
            if has_volume_spike and lower_shadow >= (2 * body) and lower_shadow > upper_shadow:
                # Body must close in the upper half of the candle
                if close_p > (high_p + low_p) / 2:
                    signal = "CALL"
                    vsa_type = "SMC-VSA (Support Wick Rejection)"
                    
    # SCENARIO B: SELL (PUT) Setup at Resistance
    if m15_downtrend and not signal:
        # Check if price is near M15 Resistance
        if high_p >= (m15_resistance - proximity_threshold):
            # Check Wick Rejection (Shooting Star-like)
            if has_volume_spike and upper_shadow >= (2 * body) and upper_shadow > lower_shadow:
                # Body must close in the lower half of the candle
                if close_p < (high_p + low_p) / 2:
                    signal = "PUT"
                    vsa_type = "SMC-VSA (Resistance Wick Rejection)"

    if signal:
        logger.info(f"VSA SCALP DETECTED: {signal} [{vsa_type}] @ {close_p} | Vol Ratio: {vol_ratio:.2f}")
        return {
            "pair": None,
            "signal": signal,
            "entry_price": close_p,
            "rsi": None,
            "stochastic": None,
            "volume_ratio": vol_ratio,
            "volume": vol,
            "epoch": c_epoch,
            "strategy_name": vsa_type
        }

    return None

def check_master_candle_strategy(candles_1m: list) -> dict:
    """
    Evaluates Strategy 5: Master Candle + Volume (Breakout & Fakeout Rejection).
    1. Finds a Master Candle (mother candle engulfing 4+ consecutive candles).
    2. Checks if the recently completed 1m candle broke out or rejected the MC range with a Volume Spike.
    """
    if not candles_1m or len(candles_1m) < 25:
        return None
        
    import pandas as pd
    from indicators import calculate_volume_metrics
    
    df_1m = pd.DataFrame(candles_1m)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df_1m[col] = pd.to_numeric(df_1m.get(col, 1.0))
        
    df_1m = calculate_volume_metrics(df_1m, 15)
    
    # Analyze the most recently COMPLETED 1m candle (-2)
    c = df_1m.iloc[-2]
    
    from strategy import is_valid_trading_session
    c_epoch = int(c['epoch'])
    if not is_valid_trading_session(c_epoch):
        return None
        
    avg_spread = (df_1m['high'].iloc[-22:-2] - df_1m['low'].iloc[-22:-2]).mean()
    if pd.isna(avg_spread) or avg_spread == 0:
        avg_spread = 0.0001
        
    # Search backwards for a valid Master Candle in the last 15 candles (index -16 to -6)
    mc_high = None
    mc_low = None
    
    for idx in range(len(df_1m) - 16, len(df_1m) - 6):
        if idx < 0:
            continue
        cand = df_1m.iloc[idx]
        c_high = float(cand['high'])
        c_low = float(cand['low'])
        
        # Must have a decent spread
        if (c_high - c_low) < (avg_spread * 1.1):
            continue
            
        # Check if next 4 candles are inside c_high and c_low
        inside_4 = df_1m.iloc[idx+1 : idx+5]
        if len(inside_4) < 4:
            continue
            
        if all(inside_4['high'] <= c_high) and all(inside_4['low'] >= c_low):
            mc_high = c_high
            mc_low = c_low
            break
            
    if not mc_high or not mc_low:
        return None
        
    open_p, close_p = float(c['open']), float(c['close'])
    high_p, low_p = float(c['high']), float(c['low'])
    vol_ratio = float(c.get('volume_ratio', 1.0))
    vol = float(c.get('volume', 1.0))
    
    spread = high_p - low_p
    body = abs(close_p - open_p)
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p
    
    signal = None
    strategy_name = ""
    
    # Must have a Volume Spike (> 1.35x average)
    has_vol_spike = vol_ratio > 1.35
    
    if not has_vol_spike:
        return None
        
    # SETUP A: High Volume True Breakout [DISABLED due to low win rate on 1m chart]
    # We only trade Trap / Wick Rejection setups which have 65%+ win rate.
        
    # SETUP B: High Volume Fakeout / Trap (Wick Rejection)
    if high_p > mc_high and close_p < mc_high and upper_shadow >= (2 * body):
        signal = "PUT"
        strategy_name = "Master Candle (Fakeout Rejection)"
    elif low_p < mc_low and close_p > mc_low and lower_shadow >= (2 * body):
        signal = "CALL"
        strategy_name = "Master Candle (Fakeout Rejection)"
        
    if signal:
        logger.info(f"MASTER CANDLE SIGNAL: {signal} [{strategy_name}] @ {close_p} | MC Range: [{mc_low:.5f} - {mc_high:.5f}]")
        return {
            "pair": None,
            "signal": signal,
            "entry_price": close_p,
            "rsi": None,
            "stochastic": None,
            "volume_ratio": vol_ratio,
            "volume": vol,
            "epoch": c_epoch,
            "strategy_name": strategy_name
        }
        
    return None

def check_order_block_retest_strategy(df: pd.DataFrame) -> dict:
    """
    Evaluates Strategy 8: wugamlo Order Block Finder (5-Consecutive Candle Institutional OB) on 5m chart.
    1. Bullish OB: A red candle followed by 5 consecutive green candles.
    2. Bearish OB: A green candle followed by 5 consecutive red candles.
    3. Pure Retest Rule: Triggers ONLY when price returns to test the OB zone without breaking the level
       with any candle body close (min(open, close) >= ob_low for CALL, max(open, close) <= ob_high for PUT).
    """
    if len(df) < 30:
        return None
        
    c_idx = len(df) - 2 # Recently completed candle
    c_candle = df.iloc[c_idx]
    c_epoch = int(c_candle['epoch'])
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    c_open = float(c_candle['open'])
    c_close = float(c_candle['close'])
    c_high = float(c_candle['high'])
    c_low = float(c_candle['low'])
    body = abs(c_close - c_open)
    lower_shadow = min(c_open, c_close) - c_low
    upper_shadow = c_high - max(c_open, c_close)
    
    periods = 5
    valid_bullish_obs = []
    valid_bearish_obs = []
    
    # We scan for OBs formed in history up to c_idx - (periods + 1)
    for i in range(1, c_idx - (periods + 1)):
        # --- 1. BULLISH ORDER BLOCK ---
        # Red candle followed by 5 green candles
        if df['close'].iloc[i] < df['open'].iloc[i]:
            subsequent_greens = all(df['close'].iloc[i+k] > df['open'].iloc[i+k] for k in range(1, periods + 1))
            if subsequent_greens:
                ob_high = float(df['open'].iloc[i]) # Top of red body
                ob_low = float(df['low'].iloc[i])   # Bottom wick
                
                # Check that OB has NOT been broken by any body close or already retested
                broken = any(min(df['open'].iloc[k], df['close'].iloc[k]) < ob_low for k in range(i + periods + 1, c_idx))
                tested = any(df['low'].iloc[k] <= (ob_high * 1.00015) for k in range(i + periods + 1, c_idx))
                if not broken and not tested:
                    valid_bullish_obs.append((ob_high, ob_low))
                    
        # --- 2. BEARISH ORDER BLOCK ---
        # Green candle followed by 5 red candles
        if df['close'].iloc[i] > df['open'].iloc[i]:
            subsequent_reds = all(df['close'].iloc[i+k] < df['open'].iloc[i+k] for k in range(1, periods + 1))
            if subsequent_reds:
                ob_high = float(df['high'].iloc[i]) # Top wick
                ob_low = float(df['open'].iloc[i])  # Bottom of green body
                
                # Check that OB has NOT been broken by any body close or already retested
                broken = any(max(df['open'].iloc[k], df['close'].iloc[k]) > ob_high for k in range(i + periods + 1, c_idx))
                tested = any(df['high'].iloc[k] >= (ob_low * 0.99985) for k in range(i + periods + 1, c_idx))
                if not broken and not tested:
                    valid_bearish_obs.append((ob_high, ob_low))
                    
    signal = None
    strategy_name = "Order Block Finder (5m Pure Retest)"
    
    # Check most recent valid Bullish OB -> CALL
    if valid_bullish_obs:
        ob_high, ob_low = valid_bullish_obs[-1]
        # Wick touches OB zone (~1.5 pips) AND body stays valid above ob_low
        if c_low <= (ob_high * 1.00015) and min(c_open, c_close) >= ob_low:
            if c_close > c_open or lower_shadow >= body:
                signal = "CALL"
                logger.info(f"WUGAMLO BULLISH OB PURE RETEST CALL @ {c_close} | OB Zone: [{ob_low:.5f} - {ob_high:.5f}]")
                
    # Check most recent valid Bearish OB -> PUT
    if not signal and valid_bearish_obs:
        ob_high, ob_low = valid_bearish_obs[-1]
        # Wick touches OB zone (~1.5 pips) AND body stays valid below ob_high
        if c_high >= (ob_low * 0.99985) and max(c_open, c_close) <= ob_high:
            if c_close < c_open or upper_shadow >= body:
                signal = "PUT"
                logger.info(f"WUGAMLO BEARISH OB PURE RETEST PUT @ {c_close} | OB Zone: [{ob_low:.5f} - {ob_high:.5f}]")
                
    if signal:
        return {
            "pair": None,
            "signal": signal,
            "entry_price": float(c_close),
            "rsi": float(c_candle.get('rsi', 50.0)),
            "stochastic": None,
            "volume_ratio": float(c_candle.get('volume_ratio', 1.0)),
            "volume": float(c_candle.get('volume', 1.0)),
            "epoch": c_epoch,
            "strategy_name": strategy_name
        }
        
    return None

def check_rsi_pivot_divergence_strategy(df: pd.DataFrame) -> dict:
    """
    Evaluates Strategy 7: ParkF RSI Pivot Divergence on 5m timeframe.
    Uses Left Bars = 15, Right Bars = 2 to detect regular bullish/bearish RSI divergence.
    """
    if len(df) < 40:
        return None
        
    c_idx = len(df) - 2 # Last completed 5m candle index
    c_candle = df.iloc[c_idx]
    c_epoch = int(c_candle['epoch'])
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    lb = 15
    rb = 2
    
    if c_idx < (lb + rb + 2):
        return None
        
    # 1. Detect Pivot Lows and Pivot Highs across available 5m history
    pls = []
    phs = []
    
    for p in range(lb, c_idx - rb + 1):
        # Pivot Low check
        left_higher_low = all(df['low'].iloc[p-k] > df['low'].iloc[p] for k in range(1, lb+1))
        right_higher_low = all(df['low'].iloc[p+k] > df['low'].iloc[p] for k in range(1, rb+1))
        if left_higher_low and right_higher_low:
            pls.append(p)
            
        # Pivot High check
        left_lower_high = all(df['high'].iloc[p-k] < df['high'].iloc[p] for k in range(1, lb+1))
        right_lower_high = all(df['high'].iloc[p+k] < df['high'].iloc[p] for k in range(1, rb+1))
        if left_lower_high and right_lower_high:
            phs.append(p)
            
    signal = None
    
    c_open = float(c_candle['open'])
    c_close = float(c_candle['close'])
    c_high = float(c_candle['high'])
    c_low = float(c_candle['low'])
    body = abs(c_close - c_open)
    lower_shadow = min(c_open, c_close) - c_low
    upper_shadow = c_high - max(c_open, c_close)
    
    # --- Check Bullish Divergence (CALL) ---
    if len(pls) >= 2:
        pl1 = pls[-2] # Older pivot low
        pl2 = pls[-1] # Newer pivot low
        
        # Exact bar trigger: Only fire on the specific candle where pl2 is freshly confirmed by rb right bars
        if c_idx == (pl2 + rb):
            price_pl1 = float(df['low'].iloc[pl1])
            price_pl2 = float(df['low'].iloc[pl2])
            rsi_pl1 = float(df['rsi'].iloc[pl1])
            rsi_pl2 = float(df['rsi'].iloc[pl2])
            
            # Price Lower Low AND RSI Higher Low (Regular Bullish Divergence)
            if price_pl2 < price_pl1 and rsi_pl2 > rsi_pl1 and rsi_pl2 <= 48.0:
                # Require confirmation rejection candle (Green close or strong lower wick)
                if c_close > c_open or lower_shadow >= body:
                    signal = "CALL"
                    logger.info(f"PARKF RSI BULLISH DIVERGENCE CALL @ {c_close} | pl1={price_pl1:.5f}(rsi={rsi_pl1:.1f}) -> pl2={price_pl2:.5f}(rsi={rsi_pl2:.1f})")

    # --- Check Bearish Divergence (PUT) ---
    if not signal and len(phs) >= 2:
        ph1 = phs[-2] # Older pivot high
        ph2 = phs[-1] # Newer pivot high
        
        # Exact bar trigger: Only fire on the specific candle where ph2 is freshly confirmed by rb right bars
        if c_idx == (ph2 + rb):
            price_ph1 = float(df['high'].iloc[ph1])
            price_ph2 = float(df['high'].iloc[ph2])
            rsi_ph1 = float(df['rsi'].iloc[ph1])
            rsi_ph2 = float(df['rsi'].iloc[ph2])
            
            # Price Higher High AND RSI Lower High (Regular Bearish Divergence)
            if price_ph2 > price_ph1 and rsi_ph2 < rsi_ph1 and rsi_ph2 >= 52.0:
                # Require confirmation rejection candle (Red close or strong upper wick)
                if c_close < c_open or upper_shadow >= body:
                    signal = "PUT"
                    logger.info(f"PARKF RSI BEARISH DIVERGENCE PUT @ {c_close} | ph1={price_ph1:.5f}(rsi={rsi_ph1:.1f}) -> ph2={price_ph2:.5f}(rsi={rsi_ph2:.1f})")
                    
    if signal:
        return {
            "pair": None,
            "signal": signal,
            "entry_price": float(c_close),
            "rsi": float(c_candle.get('rsi', 50.0)),
            "stochastic": None,
            "volume_ratio": float(c_candle.get('volume_ratio', 1.0)),
            "volume": float(c_candle.get('volume', 1.0)),
            "epoch": c_epoch,
            "strategy_name": "ParkF RSI Divergence (5m)"
        }
        
    return None

def check_mtf_smc_sniper_strategy(candles_1h: list, candles_15m: list, candles_1m: list) -> dict:
    """
    Strategy 9: Institutional Multi-Timeframe SMC Sniper (5-Minute Expiry Binary Option Setup)
    1. 1H & 15M Top-Down Directional Bias:
       - 1H and 15M must be aligned (SMA 9 vs SMA 21 and price location).
       - Prevents 'Falling Knife' counter-trend trades.
    2. Fibonacci OTE (61.8% - 79%) & Institutional Order Block (OB) on 1-Minute chart:
       - Calculates the recent structural swing impulse over the last 50 bars.
       - Identifies Bullish/Bearish Order Blocks within that impulse.
    3. Mandatory Institutional Touch + Liquidity Sweep + Rejection Candle:
       - Price MUST touch the 61.8%-79% OTE Zone OR an Order Block Zone.
       - Price MUST sweep a recent swing low (for CALL) or high (for PUT).
       - Price MUST close with a strong institutional Rejection Wick (lower_shadow >= 1.5*body / upper_shadow >= 1.5*body).
    """
    if not candles_1h or len(candles_1h) < 25 or not candles_15m or len(candles_15m) < 30 or not candles_1m or len(candles_1m) < 60:
        return None

    import pandas as pd
    # --- STEP 1: 1H & 15M PURE SMC TOP-DOWN BIAS (BOS + FVG) ---
    df_1h = pd.DataFrame(candles_1h)
    df_15m = pd.DataFrame(candles_15m)
    
    bias_1h = check_smc_structure_bias(df_1h, lookback=20)
    bias_15m = check_smc_structure_bias(df_15m, lookback=20)
    
    if bias_1h == "NEUTRAL" or bias_15m == "NEUTRAL":
        return None
        
    allow_call = (bias_1h == "BULLISH" and bias_15m == "BULLISH")
    allow_put = (bias_1h == "BEARISH" and bias_15m == "BEARISH")
    
    if not allow_call and not allow_put:
        return None
        
    # --- STEP 2: 1M STRUCTURAL SWING, OTE ZONE (61.8% - 79%), AND ORDER BLOCKS ---
    df_1m = pd.DataFrame(candles_1m)
    for col in ['open', 'high', 'low', 'close', 'volume', 'epoch']:
        df_1m[col] = pd.to_numeric(df_1m.get(col, 1.0))
        
    c_idx = len(df_1m) - 2
    c_candle = df_1m.iloc[c_idx]
    c_epoch = int(c_candle['epoch'])
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    c_open = float(c_candle['open'])
    c_close = float(c_candle['close'])
    c_high = float(c_candle['high'])
    c_low = float(c_candle['low'])
    body = abs(c_close - c_open)
    lower_shadow = min(c_open, c_close) - c_low
    upper_shadow = c_high - max(c_open, c_close)
    
    # We analyze the recent impulse over the last 50 completed candles (excluding c_idx)
    search_win = df_1m.iloc[max(0, c_idx - 50):c_idx]
    if len(search_win) < 30:
        return None
        
    swing_high = float(search_win['high'].max())
    swing_low = float(search_win['low'].min())
    swing_range = swing_high - swing_low
    
    if swing_range <= 0:
        return None
        
    # Find recent swing lows/highs for Liquidity Sweep verification (last 15 candles before c_idx)
    recent_win = df_1m.iloc[max(0, c_idx - 15):c_idx]
    recent_low = float(recent_win['low'].min())
    recent_high = float(recent_win['high'].max())
    
    signal = None
    
    # --- 3A. CHECK BULLISH MTF SMC SNIPER SETUP (CALL) ---
    if allow_call:
        # 1. Fibonacci OTE Zone (61.8% - 79.0% retracement of upward impulse)
        ote_618_call = swing_high - (0.618 * swing_range)
        ote_790_call = swing_high - (0.790 * swing_range)
        
        # 2. Bullish Order Block (last RED candle before the highest impulse green bar)
        bullish_ob_top = None
        bullish_ob_bot = None
        for k in range(len(search_win) - 2, 0, -1):
            row = search_win.iloc[k]
            if row['close'] < row['open']:  # Red candle
                # Check if followed by displacement up
                if search_win.iloc[k+1]['close'] > search_win.iloc[k+1]['open']:
                    bullish_ob_top = float(row['open'])
                    bullish_ob_bot = float(row['low'])
                    break
                    
        # 3. Mandatory Institutional Touch: c_low must dip into OTE Zone OR Bullish Order Block
        touched_ote = (ote_790_call <= c_low <= (ote_618_call * 1.00015))
        touched_ob = False
        if bullish_ob_top and bullish_ob_bot:
            touched_ob = (bullish_ob_bot <= c_low <= (bullish_ob_top * 1.00015))
            
        if touched_ote or touched_ob:
            # 4. Liquidity Sweep: c_low swept below recent swing low OR touched deep zone, but body rejected above
            swept_liquidity = (c_low <= recent_low) or (c_low <= ote_790_call)
            if swept_liquidity:
                # 5. Rejection Candle: Strong lower wick (>= 1.5x body) AND positive/favorable close
                if lower_shadow >= (1.5 * body) and c_close >= (c_low + (c_high - c_low) * 0.45):
                    signal = "CALL"
                    logger.info(f"🎯 MTF SMC SNIPER CALL @ {c_close} | 1H+15M Bullish | Touched OTE:{touched_ote} OB:{touched_ob} | Sweep Low:{recent_low:.5f}")

    # --- 3B. CHECK BEARISH MTF SMC SNIPER SETUP (PUT) ---
    if not signal and allow_put:
        # 1. Fibonacci OTE Zone (61.8% - 79.0% retracement of downward impulse)
        ote_618_put = swing_low + (0.618 * swing_range)
        ote_790_put = swing_low + (0.790 * swing_range)
        
        # 2. Bearish Order Block (last GREEN candle before the lowest impulse red bar)
        bearish_ob_top = None
        bearish_ob_bot = None
        for k in range(len(search_win) - 2, 0, -1):
            row = search_win.iloc[k]
            if row['close'] > row['open']:  # Green candle
                # Check if followed by displacement down
                if search_win.iloc[k+1]['close'] < search_win.iloc[k+1]['open']:
                    bearish_ob_top = float(row['high'])
                    bearish_ob_bot = float(row['open'])
                    break
                    
        # 3. Mandatory Institutional Touch: c_high must reach into OTE Zone OR Bearish Order Block
        touched_ote = ((ote_618_put * 0.99985) <= c_high <= ote_790_put)
        touched_ob = False
        if bearish_ob_top and bearish_ob_bot:
            touched_ob = ((bearish_ob_bot * 0.99985) <= c_high <= bearish_ob_top)
            
        if touched_ote or touched_ob:
            # 4. Liquidity Sweep: c_high swept above recent swing high OR touched deep zone, but body rejected below
            swept_liquidity = (c_high >= recent_high) or (c_high >= ote_790_put)
            if swept_liquidity:
                # 5. Rejection Candle: Strong upper wick (>= 1.5x body) AND negative/favorable close
                if upper_shadow >= (1.5 * body) and c_close <= (c_low + (c_high - c_low) * 0.55):
                    signal = "PUT"
                    logger.info(f"🎯 MTF SMC SNIPER PUT @ {c_close} | 1H+15M Bearish | Touched OTE:{touched_ote} OB:{touched_ob} | Sweep High:{recent_high:.5f}")

    if signal:
        return {
            "pair": None,
            "signal": signal,
            "entry_price": float(c_close),
            "rsi": float(c_candle.get('rsi', 50.0)),
            "stochastic": float(c_candle.get('stochastic', 50.0)),
            "volume_ratio": float(c_candle.get('volume_ratio', 1.0)),
            "volume": float(c_candle.get('volume', 1.0)),
            "epoch": c_epoch,
            "strategy_name": "MTF SMC Sniper (5m Expiry)"
        }
        
    return None
