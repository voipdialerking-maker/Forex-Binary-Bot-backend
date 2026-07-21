import logging
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import config as config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Database")

supabase_client: Client = None

if config.SUPABASE_URL and config.SUPABASE_KEY:
    try:
        supabase_client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        logger.info("Supabase Client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing Supabase client: {e}")
else:
    logger.warning("Supabase URL or Key not set. Database operations will be bypassed.")

def insert_signal(pair: str, direction: str, entry_price: float, rsi: float, stochastic: float, volume: float, strategy: str) -> str:
    """
    Inserts a new signal into the Supabase database.
    Returns the generated UUID (id) of the signal, or None on failure.
    """
    if not supabase_client:
        logger.warning("Supabase client not active. Skipping DB insert.")
        return None

    now = datetime.now(timezone.utc)
    expiry_time = now + timedelta(minutes=5)

    data = {
        "pair": pair,
        "type": direction,  # "CALL" or "PUT"
        "entry_price": float(entry_price),
        "expiry_price": None,
        "status": "ACTIVE",
        "rsi_value": float(rsi) if rsi is not None else None,
        "stochastic_k": float(stochastic) if stochastic is not None else None,
        "volume_value": float(volume) if volume is not None else None,
        "created_at": now.isoformat(),
        "expiry_time": expiry_time.isoformat(),
        "strategy": strategy
    }

    try:
        response = supabase_client.table("signals").insert(data).execute()
        if response.data and len(response.data) > 0:
            inserted_id = response.data[0]["id"]
            logger.info(f"Signal stored in Supabase: {pair} {direction} ID: {inserted_id}")
            return inserted_id
    except Exception as e:
        logger.error(f"Failed to insert signal into Supabase: {e}")
    
    return None

def update_signal_outcome(signal_id: str, expiry_price: float, outcome: str):
    """
    Updates the signal's status and expiry price after evaluation.
    outcome: "WON", "LOST", or "TIE"
    """
    if not supabase_client or not signal_id:
        return

    try:
        supabase_client.table("signals").update({
            "expiry_price": float(expiry_price),
            "status": outcome
        }).eq("id", signal_id).execute()
        logger.info(f"Updated signal {signal_id} outcome to {outcome} at price {expiry_price}")
    except Exception as e:
        logger.error(f"Failed to update signal outcome in Supabase: {e}")

def delete_old_signals():
    """
    Deletes signals older than 30 days (1 month) from the database.
    """
    if not supabase_client:
        return

    one_month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        response = supabase_client.table("signals").delete().lt("created_at", one_month_ago.isoformat()).execute()
        deleted_count = len(response.data) if response.data else 0
        logger.info(f"Cleaned up {deleted_count} old signals (older than 1 month).")
    except Exception as e:
        logger.error(f"Failed to run database cleanup: {e}")
