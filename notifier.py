import requests
import logging
import backend.config as config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Notifier")

def send_telegram_signal(pair_display: str, direction: str, entry_price: float, rsi: float, stochastic: float, volume: float) -> bool:
    """
    Sends a formatted signal notification to Telegram via the Telegram Bot API.
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram Bot Token or Chat ID not configured. Skipping Telegram notification.")
        return False

    emoji = "🟢 <b>CALL</b> (BUY)" if direction == "CALL" else "🔴 <b>PUT</b> (SELL)"
    
    # Format message with HTML tags
    message = (
        f"{emoji}\n"
        f"<b>Pair:</b> {pair_display}\n"
        f"<b>Timeframe:</b> 5 Minutes\n"
        f"<b>Entry Price:</b> {entry_price:.5f}\n\n"
        f"<b>Indicators at Setup:</b>\n"
        f"• RSI (14): {rsi:.2f}\n"
        f"• Stochastic K (14): {stochastic:.2f}\n"
        f"• Volume Multiplier: {volume:.2f}x\n\n"
        f"⚠️ <i>Please trade on your Demo account first. Always follow strict risk management.</i>"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"Telegram signal successfully sent for {pair_display}.")
            return True
        else:
            logger.error(f"Telegram API error ({response.status_code}): {response.text}")
    except Exception as e:
        logger.error(f"Exception while sending Telegram signal: {e}")

    return False

def send_telegram_outcome(pair_display: str, direction: str, entry_price: float, expiry_price: float, outcome: str) -> bool:
    """
    Sends an outcome update (WON/LOST/TIE) to Telegram after the 5-minute trade expiry.
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        return False

    emoji_outcome = "✅ <b>WON</b>" if outcome == "WON" else "❌ <b>LOST</b>" if outcome == "LOST" else "⚪ <b>TIE</b>"
    dir_emoji = "🟢" if direction == "CALL" else "🔴"

    message = (
        f"{emoji_outcome}\n"
        f"<b>Pair:</b> {pair_display}\n"
        f"<b>Type:</b> {dir_emoji} {direction}\n"
        f"<b>Entry Price:</b> {entry_price:.5f}\n"
        f"<b>Expiry Price:</b> {expiry_price:.5f}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Exception while sending Telegram outcome: {e}")
        return False
