import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://jzjhdjstlokbgklmxlgv.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_4neSllJ9YkupZ-VgpC9ZJQ_2OPCE0ha")

# Telegram Bot Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Deriv API Config
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089")
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

# Trading pairs to monitor (Deriv symbols for Forex)
MONITORED_PAIRS = [
    "frxEURUSD",
    "frxGBPUSD",
    "frxAUDUSD",
    "frxUSDJPY",
    "frxEURJPY",
    "frxGBPJPY"
]

# Indicator Config
BOLLINGER_PERIOD = 20
BOLLINGER_DEV = 2.0
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
STOCH_OVERBOUGHT = 80
STOCH_OVERSOLD = 20
VOLUME_MA_PERIOD = 20
VOLUME_CLIMAX_MULTIPLIER = 1.5 # 1.5x of moving average volume

# Strategy general config
TIMEFRAME = "5m"  # 5 minutes
