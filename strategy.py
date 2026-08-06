import pandas as pd
import numpy as np
import logging
import config as config
from datetime import datetime, timezone
from indicators import calculate_ema, calculate_sma, calculate_rsi, calculate_stochastic

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

    # --- STRICT 5-BAR MOMENTUM SAFETY SHIELD ---
    # Never declare BULLISH if price is actively dumping over the last 5 bars
    last_5_change = float(win['close'].iloc[-1]) - float(win['close'].iloc[-min(6, len(win))])
    if last_5_change < 0 and struct_bullish:
        struct_bullish = False
    # Never declare BEARISH if price is actively surging over the last 5 bars
    if last_5_change > 0 and struct_bearish:
        struct_bearish = False

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
        
    df_1m = calculate_rsi(df_1m)
    df_1m = calculate_stochastic(df_1m)
    vol_ma = df_1m['volume'].rolling(window=20).mean()
    df_1m['volume_ratio'] = df_1m['volume'] / vol_ma.replace(0, 1.0)
        
    c_idx = len(df_1m) - 2
    c_candle = df_1m.iloc[c_idx]
    c_epoch = int(c_candle['epoch'])
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    # Require Institutional Volume Spike (>= 1.20x average volume) to prevent low-volume drift sweeps
    if float(c_candle.get('volume_ratio', 1.0)) < 1.20:
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
                # 5. Rejection Candle: Strong lower wick (>= 1.5x body), GREEN candle close, AND no 3-bar dumping
                is_green_rejection = (c_close > c_open) and (lower_shadow >= (1.5 * body)) and (c_close >= (c_low + (c_high - c_low) * 0.50))
                recent_3_dumping = all(df_1m['close'].iloc[c_idx-m] < df_1m['open'].iloc[c_idx-m] for m in range(1, 4))
                if is_green_rejection and not recent_3_dumping and float(c_candle.get('rsi', 50.0)) <= 68.0:
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
                # 5. Rejection Candle: Strong upper wick (>= 1.5x body), RED candle close, AND no 3-bar green surge
                is_red_rejection = (c_close < c_open) and (upper_shadow >= (1.5 * body)) and (c_close <= (c_low + (c_high - c_low) * 0.40))
                recent_3_surging = all(df_1m['close'].iloc[c_idx-m] > df_1m['open'].iloc[c_idx-m] for m in range(1, 4))
                if is_red_rejection and not recent_3_surging and float(c_candle.get('rsi', 50.0)) >= 32.0:
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

def check_master_dollar_compass_allow(pair: str, signal_type: str, eurusd_1h: list, eurusd_15m: list) -> bool:
    """
    Master EUR/USD Dollar Compass Shield:
    1. Checks the Master Institutional DXY / EUR/USD trend using Pure BOS + FVG on 1H and 15M timeframes.
    2. Protects USD-Quote pairs ('GBP/USD', 'AUD/USD', 'NZD/USD', 'EUR/USD') from taking trades against Dollar liquidity:
       - If EUR/USD 1H & 15M is BULLISH (Dollar weakening) -> Blocks PUT (SELL) signals.
       - If EUR/USD 1H & 15M is BEARISH (Dollar strengthening) -> Blocks CALL (BUY) signals.
    3. Protects USD-Base pairs ('USD/JPY', 'USD/CAD', 'USD/CHF') via inverse correlation:
       - Blocks CALL when EUR/USD is BULLISH; Blocks PUT when EUR/USD is BEARISH.
    Returns True if trade is allowed (aligned with Master Compass or Neutral), False if blocked.
    """
    if not pair or not signal_type or not eurusd_1h or not eurusd_15m:
        return True
        
    p_upper = pair.upper()
    # Don't filter non-dollar pairs or cross pairs here
    is_usd_quote = any(q in p_upper for q in ["EUR/USD", "GBP/USD", "AUD/USD", "NZD/USD"])
    is_usd_base = any(b in p_upper for b in ["USD/JPY", "USD/CAD", "USD/CHF"])
    
    if not is_usd_quote and not is_usd_base:
        return True
        
    import pandas as pd
    bias_1h = check_smc_structure_bias(pd.DataFrame(eurusd_1h), lookback=20)
    bias_15m = check_smc_structure_bias(pd.DataFrame(eurusd_15m), lookback=20)
    
    # Require clear macro alignment on EUR/USD to block counter-dollar trades
    eurusd_bullish = (bias_1h == "BULLISH" and bias_15m == "BULLISH")
    eurusd_bearish = (bias_1h == "BEARISH" and bias_15m == "BEARISH")
    
    if not eurusd_bullish and not eurusd_bearish:
        return True # Neutral dollar bias -> allow strategy's own rules to govern
        
    if is_usd_quote:
        if eurusd_bullish and signal_type == "PUT":
            logger.info(f"🚫 MASTER DOLLAR COMPASS SHIELD: Blocked PUT on {pair} because EUR/USD 1H+15M is BULLISH!")
            return False
        if eurusd_bearish and signal_type == "CALL":
            logger.info(f"🚫 MASTER DOLLAR COMPASS SHIELD: Blocked CALL on {pair} because EUR/USD 1H+15M is BEARISH!")
            return False
            
    elif is_usd_base:
        if eurusd_bullish and signal_type == "CALL":
            logger.info(f"🚫 MASTER DOLLAR COMPASS SHIELD: Blocked CALL on {pair} (USD-Base) because EUR/USD 1H+15M is BULLISH!")
            return False
        if eurusd_bearish and signal_type == "PUT":
            logger.info(f"🚫 MASTER DOLLAR COMPASS SHIELD: Blocked PUT on {pair} (USD-Base) because EUR/USD 1H+15M is BEARISH!")
            return False
            
    return True

def check_smt_divergence_sniper(pair: str, candles_1m: list, eurusd_1m: list) -> dict:
    """
    Strategy 10: Institutional SMT (Smart Money Tool) Divergence Sniper (5-Minute Expiry)
    1. Compares the last 15 candles of target pair ('GBP/USD' or 'AUD/USD') against Master 'EUR/USD'.
    2. SMT BULLISH DIVERGENCE (CALL):
       - EUR/USD makes a Lower Low over the lookback window.
       - Target pair refuses to drop (makes a Higher Low), showing institutional accumulation.
       - Rejection wick confirmed -> High probability 5m CALL.
    3. SMT BEARISH DIVERGENCE (PUT):
       - EUR/USD makes a Higher High over the lookback window.
       - Target pair refuses to rally (makes a Lower High), showing institutional distribution.
       - Rejection wick confirmed -> High probability 5m PUT.
    """
    if not pair or "EUR/USD" in pair.upper() or not candles_1m or len(candles_1m) < 40 or not eurusd_1m or len(eurusd_1m) < 40:
        return None
        
    p_upper = pair.upper()
    if not any(q in p_upper for q in ["GBP/USD", "AUD/USD"]):
        return None
        
    import pandas as pd
    df_p = pd.DataFrame(candles_1m)
    df_e = pd.DataFrame(eurusd_1m)
    
    for col in ['open', 'high', 'low', 'close', 'volume', 'epoch']:
        df_p[col] = pd.to_numeric(df_p.get(col, 1.0))
        df_e[col] = pd.to_numeric(df_e.get(col, 1.0))
        
    df_p = calculate_rsi(df_p)
    df_p = calculate_stochastic(df_p)
    vol_ma_p = df_p['volume'].rolling(window=20).mean()
    df_p['volume_ratio'] = df_p['volume'] / vol_ma_p.replace(0, 1.0)
        
    c_idx = len(df_p) - 2
    e_idx = len(df_e) - 2
    if c_idx < 25 or e_idx < 25:
        return None
        
    c_candle = df_p.iloc[c_idx]
    c_epoch = int(c_candle['epoch'])
    
    if not is_valid_trading_session(c_epoch):
        return None
        
    if float(c_candle.get('volume_ratio', 1.0)) < 1.20:
        return None
        
    c_open = float(c_candle['open'])
    c_close = float(c_candle['close'])
    c_high = float(c_candle['high'])
    c_low = float(c_candle['low'])
    body = abs(c_close - c_open)
    lower_shadow = min(c_open, c_close) - c_low
    upper_shadow = c_high - max(c_open, c_close)
    
    # Compare structure of last 15 bars vs prior 15 bars
    w_curr_p = df_p.iloc[c_idx-15:c_idx]
    w_prev_p = df_p.iloc[c_idx-30:c_idx-15]
    
    w_curr_e = df_e.iloc[e_idx-15:e_idx]
    w_prev_e = df_e.iloc[e_idx-30:e_idx-15]
    
    if len(w_prev_p) < 10 or len(w_prev_e) < 10:
        return None
        
    p_curr_low = float(w_curr_p['low'].min())
    p_prev_low = float(w_prev_p['low'].min())
    p_curr_high = float(w_curr_p['high'].max())
    p_prev_high = float(w_prev_p['high'].max())
    
    e_curr_low = float(w_curr_e['low'].min())
    e_prev_low = float(w_prev_e['low'].min())
    e_curr_high = float(w_curr_e['high'].max())
    e_prev_high = float(w_prev_e['high'].max())
    
    signal = None
    
    # 1. BULLISH SMT DIVERGENCE (CALL):
    # EUR/USD made Lower Low (e_curr_low < e_prev_low), but pair made Higher Low (p_curr_low >= p_prev_low)
    if (e_curr_low < e_prev_low) and (p_curr_low >= (p_prev_low * 0.99995)):
        # Require institutional rejection wick on current bar
        if lower_shadow >= (1.3 * body) and c_close > c_open:
            signal = "CALL"
            logger.info(f"💎 SMT BULLISH DIVERGENCE CALL on {pair} @ {c_close} | EUR/USD made Lower Low, but {pair} held Higher Low!")
            
    # 2. BEARISH SMT DIVERGENCE (PUT):
    # EUR/USD made Higher High (e_curr_high > e_prev_high), but pair made Lower High (p_curr_high <= p_prev_high)
    elif (e_curr_high > e_prev_high) and (p_curr_high <= (p_prev_high * 1.00005)):
        # Require institutional rejection wick on current bar
        if upper_shadow >= (1.3 * body) and c_close < c_open:
            signal = "PUT"
            logger.info(f"💎 SMT BEARISH DIVERGENCE PUT on {pair} @ {c_close} | EUR/USD made Higher High, but {pair} held Lower High!")
            
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
            "strategy_name": "SMT Divergence Sniper (5m Expiry)"
        }
        
    return None

def check_1m_master_pullback_sniper(pair: str, candles_1m: list, candles_15m: list = None) -> dict:
    """
    Unbeatable 1-Minute Master Pullback Strategy (Evaluated on 5m boundaries)
    Monitors 3 structural zones with 4 Institutional Filters:
    1. 15m MTF Alignment
    2. Volume Breakout Anomaly
    3. Momentum Crash Check
    4. London/NY Session Filter
    """
    if not candles_1m or len(candles_1m) < 150:
        return None
        
    import pandas as pd
    from datetime import datetime, timezone
    
    df = pd.DataFrame(candles_1m)
    for col in ['open', 'high', 'low', 'close', 'volume', 'epoch']:
        df[col] = pd.to_numeric(df.get(col, 1.0))
        
    # --- FILTER D: Session Time ---
    current_idx = len(df) - 2
    if current_idx < 10:
        return None
    c = df.iloc[current_idx]
    current_time = datetime.fromtimestamp(int(c['epoch']), timezone.utc)
    if current_time.hour < 7 or current_time.hour >= 21:
        return None # Only trade London/NY sessions
        
    # Calculate 1m EMAs and Volume MA
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    vol_ma = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / vol_ma.replace(0, 1.0)
    
    trend_up = float(c['ema_50']) > float(c['ema_200'])
    trend_down = float(c['ema_50']) < float(c['ema_200'])
    
    # --- FILTER A: 15m MTF Alignment ---
    if candles_15m and len(candles_15m) > 150:
        df_15 = pd.DataFrame(candles_15m)
        df_15['close'] = pd.to_numeric(df_15['close'])
        df_15['ema_50'] = df_15['close'].ewm(span=50, adjust=False).mean()
        df_15['ema_200'] = df_15['close'].ewm(span=200, adjust=False).mean()
        c15 = df_15.iloc[-2]
        mtf_up = float(c15['ema_50']) > float(c15['ema_200'])
        mtf_down = float(c15['ema_50']) < float(c15['ema_200'])
        
        if trend_up and not mtf_up:
            return None # Fake 1m uptrend
        if trend_down and not mtf_down:
            return None # Fake 1m downtrend
            
    if not trend_up and not trend_down:
        return None
        
    # Identify Pivots (Left 4, Right 4)
    left_bars = 4
    right_bars = 4
    all_pivots = []
    
    for i in range(left_bars, len(df) - right_bars - 1):
        c_high = float(df['high'].iloc[i])
        c_low = float(df['low'].iloc[i])
        is_ph = True
        is_pl = True
        for j in range(1, left_bars + 1):
            if float(df['high'].iloc[i-j]) >= c_high: is_ph = False
            if float(df['low'].iloc[i-j]) <= c_low: is_pl = False
        for j in range(1, right_bars + 1):
            if float(df['high'].iloc[i+j]) >= c_high: is_ph = False
            if float(df['low'].iloc[i+j]) <= c_low: is_pl = False
        if is_ph: all_pivots.append({'type': 'PH', 'idx': i, 'val': c_high})
        if is_pl: all_pivots.append({'type': 'PL', 'idx': i, 'val': c_low})
        
    if len(all_pivots) < 3: return None
    all_pivots.sort(key=lambda x: x['idx'])
    
    signal = None
    zone_hit = ""
    
    trigger_start_idx = max(0, current_idx - 4)
    used_check_end_idx = trigger_start_idx - 1
    
    if trend_up:
        last_ph = None
        last_pl = None
        prev_ph = None
        
        valid_pivots = [p for p in all_pivots if p['idx'] <= used_check_end_idx]
        
        for p in reversed(valid_pivots):
            if p['type'] == 'PH':
                if not last_ph: last_ph = p
                elif not prev_ph: prev_ph = p
            elif p['type'] == 'PL':
                if last_ph and not last_pl: last_pl = p
                
        if not (last_ph and last_pl and prev_ph): return None
        if last_pl['idx'] > last_ph['idx']:
            pl_candidates = [p for p in valid_pivots if p['type'] == 'PL' and p['idx'] < last_ph['idx']]
            if pl_candidates: last_pl = pl_candidates[-1]
            else: return None
            
        idx_end = last_ph['idx']
        idx_start = last_pl['idx']
        rally_end_val = last_ph['val']
        rally_start_val = last_pl['val']
        
        if rally_end_val <= prev_ph['val']: return None # No BOS
        
        # --- FILTER B: Volume Anomaly ---
        # The breakout peak candle must have decent volume > 1.1x avg
        if df['volume_ratio'].iloc[idx_end] < 1.1:
            return None # Weak breakout, trap likely
            
        # --- FILTER C: Pullback Momentum ---
        # Did it crash from rally top in less than 3 candles?
        if current_idx - idx_end < 3:
            return None # Too fast, catching a falling knife
        
        diff = rally_end_val - rally_start_val
        fibo_0_5 = rally_end_val - (diff * 0.5)
        fibo_0_618 = rally_end_val - (diff * 0.618)
        
        first_hh_val = None
        ob_high = None
        ob_low = None
        
        for i in range(idx_start + 1, idx_end - 1):
            if df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i+1]:
                first_hh_val = df['high'].iloc[i]
                pullback_low_val = 999999
                pullback_low_idx = i
                for j in range(i + 1, idx_end):
                    if df['high'].iloc[j] > first_hh_val: break
                    if df['low'].iloc[j] < pullback_low_val:
                        pullback_low_val = df['low'].iloc[j]
                        pullback_low_idx = j
                for k in range(pullback_low_idx, i, -1):
                    if df['close'].iloc[k] < df['open'].iloc[k]:
                        ob_high = df['high'].iloc[k]
                        ob_low = df['low'].iloc[k]
                        break
                break
                
        fibo_used = False
        ob_used = False
        hh_used = False
        
        for i in range(idx_end + 1, used_check_end_idx + 1):
            l = df['low'].iloc[i]
            if l < rally_start_val: return None # CHoCH before window, wait for new BOS
            if first_hh_val and l <= first_hh_val: hh_used = True
            if ob_high and l <= ob_high: ob_used = True
            if l <= fibo_0_5: fibo_used = True
            
        for i in range(trigger_start_idx, current_idx + 1):
            l = df['low'].iloc[i]
            if l < rally_start_val: return None # CHoCH happened during window
            
            if not signal:
                if first_hh_val and not hh_used and l <= first_hh_val * 1.0001:
                    signal = "CALL"
                    zone_hit = "First HH Resistance-turned-Support"
                elif ob_high and not ob_used and l <= ob_high * 1.0001:
                    signal = "CALL"
                    zone_hit = "Order Block (Last Red Candle)"
                elif not fibo_used and l <= fibo_0_5 * 1.0001 and df['close'].iloc[i] >= fibo_0_618 * 0.9995:
                    signal = "CALL"
                    zone_hit = "Golden Fibo (0.5 - 0.618)"
            
    elif trend_down:
        last_pl = None
        last_ph = None
        prev_pl = None
        
        valid_pivots = [p for p in all_pivots if p['idx'] <= used_check_end_idx]
        
        for p in reversed(valid_pivots):
            if p['type'] == 'PL':
                if not last_pl: last_pl = p
                elif not prev_pl: prev_pl = p
            elif p['type'] == 'PH':
                if last_pl and not last_ph: last_ph = p
                
        if not (last_pl and last_ph and prev_pl): return None
        if last_ph['idx'] > last_pl['idx']:
            ph_candidates = [p for p in valid_pivots if p['type'] == 'PH' and p['idx'] < last_pl['idx']]
            if ph_candidates: last_ph = ph_candidates[-1]
            else: return None
            
        idx_end = last_pl['idx']
        idx_start = last_ph['idx']
        rally_end_val = last_pl['val']
        rally_start_val = last_ph['val']
        
        if rally_end_val >= prev_pl['val']: return None
        
        # --- FILTER B: Volume Anomaly ---
        if df['volume_ratio'].iloc[idx_end] < 1.1:
            return None # Weak breakout, trap likely
            
        # --- FILTER C: Pullback Momentum ---
        if current_idx - idx_end < 3:
            return None # Too fast, catching a falling knife
        
        diff = rally_start_val - rally_end_val
        fibo_0_5 = rally_end_val + (diff * 0.5)
        fibo_0_618 = rally_end_val + (diff * 0.618)
        
        first_ll_val = None
        ob_high = None
        ob_low = None
        
        for i in range(idx_start + 1, idx_end - 1):
            if df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i+1]:
                first_ll_val = df['low'].iloc[i]
                pullback_high_val = -1
                pullback_high_idx = i
                for j in range(i + 1, idx_end):
                    if df['low'].iloc[j] < first_ll_val: break
                    if df['high'].iloc[j] > pullback_high_val:
                        pullback_high_val = df['high'].iloc[j]
                        pullback_high_idx = j
                for k in range(pullback_high_idx, i, -1):
                    if df['close'].iloc[k] > df['open'].iloc[k]:
                        ob_high = df['high'].iloc[k]
                        ob_low = df['low'].iloc[k]
                        break
                break
                
        fibo_used = False
        ob_used = False
        ll_used = False
        
        for i in range(idx_end + 1, used_check_end_idx + 1):
            h = df['high'].iloc[i]
            if h > rally_start_val: return None
            if first_ll_val and h >= first_ll_val: ll_used = True
            if ob_low and h >= ob_low: ob_used = True
            if h >= fibo_0_5: fibo_used = True
            
        for i in range(trigger_start_idx, current_idx + 1):
            h = df['high'].iloc[i]
            if h > rally_start_val: return None
            
            if not signal:
                if first_ll_val and not ll_used and h >= first_ll_val * 0.9999:
                    signal = "PUT"
                    zone_hit = "First LL Support-turned-Resistance"
                elif ob_low and not ob_used and h >= ob_low * 0.9999:
                    signal = "PUT"
                    zone_hit = "Order Block (Last Green Candle)"
                elif not fibo_used and h >= fibo_0_5 * 0.9999 and df['close'].iloc[i] <= fibo_0_618 * 1.0005:
                    signal = "PUT"
                    zone_hit = "Golden Fibo (0.5 - 0.618)"
            
    if signal:
        import logging
        logger = logging.getLogger("Main")
        logger.info(f"⚡ MEGA MASTER PULLBACK SNIPER {signal} on {pair} @ {c['close']} | Zone: {zone_hit}")
        return {
            "pair": pair,
            "signal": signal,
            "entry_price": float(c['close']),
            "rsi": 50.0,
            "stochastic": 50.0,
            "volume_ratio": float(c.get('volume_ratio', 1.0)),
            "volume": float(c['volume']),
            "epoch": int(c['epoch']),
            "strategy_name": f"1m Master Pullback Sniper ({zone_hit})"
        }
        
    return None
        
    import pandas as pd
    df = pd.DataFrame(candles_5m)
    for col in ['open', 'high', 'low', 'close', 'volume', 'epoch']:
        df[col] = pd.to_numeric(df.get(col, 1.0))
        
    df = calculate_rsi(df)
    df = calculate_stochastic(df)
    vol_ma = df['volume'].rolling(window=20).mean()
    df['volume_ratio'] = df['volume'] / vol_ma.replace(0, 1.0)
    
    # Calculate EMA Trend Filter
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    idx = len(df) - 2
    if idx < 10:
        return None
        
    c = df.iloc[idx]       # Current completed 5m candle
    p = df.iloc[idx - 1]   # Previous 5m candle
    p5 = df.iloc[idx - 5]  # 5 bars ago (open[5])
    
    c_epoch = int(c['epoch'])
    if not is_valid_trading_session(c_epoch):
        # return None
        pass
        
    if float(c.get('volume_ratio', 1.0)) < 1.20:
        # return None
        pass
        
    c_open, c_close = float(c['open']), float(c['close'])
    p_open, p_close = float(p['open']), float(p['close'])
    p5_open = float(p5['open'])
    
    rsi = float(c.get('rsi', 50.0))
    stochastic = float(c.get('stochastic', 50.0))
    vol_ratio = float(c.get('volume_ratio', 1.0))
    volume = float(c.get('volume', 1.0))
    
    ema_50 = float(c.get('ema_50', 0.0))
    ema_200 = float(c.get('ema_200', 0.0))
    trend_up = ema_50 > ema_200
    trend_down = ema_50 < ema_200
    
    signal = None
    
    # 1. Bullish Harami (CALL):
    # (open[1] > close[1] and close > open and close <= open[1] and close[1] <= open and close - open < open[1] - close[1] and open[5] > open)
    is_prev_red = (p_open > p_close)
    is_curr_green = (c_close > c_open)
    is_inside_bull = (c_close <= p_open) and (p_close <= c_open)
    is_smaller_bull = (c_close - c_open) < (p_open - p_close)
    trend_exhausted_bull = (p5_open > c_open)
    
    if is_prev_red and is_curr_green and is_inside_bull and is_smaller_bull and trend_exhausted_bull and trend_up: # and rsi <= 68.0:
        signal = "CALL"
        logger.info(f"🎯 5M HARAMI SMC SNIPER CALL on {pair} @ {c_close} | Bullish Inside Bar after 5m Downtrend Exhaustion (EMA Trend Up)")
        
    # 2. Bearish Harami (PUT):
    # (close[1] > open[1] and open > close and open <= close[1] and open[1] <= close and open - close < close[1] - open[1] and open[5] < open)
    is_prev_green = (p_close > p_open)
    is_curr_red = (c_open > c_close)
    is_inside_bear = (c_open <= p_close) and (p_open <= c_close)
    is_smaller_bear = (c_open - c_close) < (p_close - p_open)
    trend_exhausted_bear = (p5_open < c_open)
    
    if not signal and is_prev_green and is_curr_red and is_inside_bear and is_smaller_bear and trend_exhausted_bear and trend_down: # and rsi >= 32.0:
        signal = "PUT"
        logger.info(f"🎯 5M HARAMI SMC SNIPER PUT on {pair} @ {c_close} | Bearish Inside Bar after 5m Uptrend Exhaustion (EMA Trend Down)")
        
    if signal:
        return {
            "pair": None,
            "signal": signal,
            "entry_price": float(c_close),
            "rsi": rsi,
            "stochastic": stochastic,
            "volume_ratio": vol_ratio,
            "volume": volume,
            "epoch": c_epoch,
            "strategy_name": "5m Harami SMC Sniper (5m Expiry)"
        }
        
    return None



