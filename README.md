# Quantum Bot - 5-Minute Binary Options Signal Generator

Quantum Bot is a premium, full-stack **5-Minute Binary Options Signal Generator**. It continuously streams live market data from the Deriv WebSocket API, computes technical indicators, detects high-probability trade setups, and sends alerts to Telegram while rendering them in real-time on a modern Glassmorphism React dashboard.

---

## 🚀 Key Features

* **Zero-Configuration Live Feed:** Streams real-time tick and candle data directly from Deriv WebSockets (using the public App ID `1089`). No MetaTrader app installation required.
* **Advanced Indicator Strategy:** Triggers alerts based on:
  - **Bollinger Bands** (Extreme price boundaries)
  - **RSI (14)** (Momentum overbought/oversold)
  - **Stochastic Oscillator (14, 3)** (Reversal confirmation)
  - **Volume Climax** (Validates breakout strength; auto-bypassed on asset classes lacking volume details like Forex).
* **Autonomous Outcome Validation:** Tracks generated signals for exactly 5 minutes, checks the final closing price, and labels them as **WON**, **LOST**, or **TIE** in the database and on Telegram.
* **Realtime Premium Dashboard:** Built with React & Vanilla CSS featuring a gorgeous futuristic dark glassmorphism design. Includes:
  - **Searching Pairs:** Grid tracking assets under monitoring with pulse animations.
  - **Active Signals:** Live trade cards displaying remaining time using active 5-minute countdowns.
  - **Signal History:** Displays 1-week records with summary statistics (Total, Won, Lost, and Win Rate) and detailed click-to-view modal.
* **Database & Automatic Cleanup:** Uses Supabase for storing records and running scheduled queries to delete signals older than 1 week.
* **Instant Notifications:** Automatically sends alerts and post-trade outcomes to your Telegram group/channel.

---

## 📁 Directory Structure

```text
/Binary
  ├── backend/
  │    ├── config.py           # Configuration parameters and thresholds
  │    ├── database.py         # Supabase database operations & cleanup
  │    ├── data_feed.py        # WebSocket connector to Deriv API
  │    ├── indicators.py       # Indicator math (BB, RSI, Stochastic) using Pandas
  │    ├── notifier.py         # Telegram message formatter and poster
  │    ├── strategy.py         # CALL/PUT logic
  │    ├── main.py             # Main entry point and outcome tracker
  │    ├── test_data_feed.py   # One-shot dry-run calculation script
  │    └── requirements.txt    # Python dependencies
  ├── frontend/
  │    ├── src/
  │    │    ├── components/    # Navbar, ActiveSignals, SearchingPairs, History, etc.
  │    │    ├── App.jsx        # Main component with Supabase subscription
  │    │    ├── App.css        # Premium Vanilla CSS styles
  │    │    └── main.jsx       # React mounting
  │    ├── index.html          # Main HTML entry with SEO tags
  │    ├── package.json        # Frontend NPM configurations
  │    └── vite.config.js      # Vite dev settings
  ├── supabase_setup.sql       # Database schema & realtime config
  ├── guideme.md               # Detailed step-by-step setup guide
  └── requirements.txt         # Root-level Python dependencies
```

---

## 🛠️ Quick Start

For detailed step-by-step credentials acquisition and deployment, read the [Setup Guide (guideme.md)](./guideme.md).

### 1. Database Setup
Copy the contents of `supabase_setup.sql` and run them in your Supabase SQL Editor.

### 2. Configure Backend
Create/modify the `.env` file in the `backend/` folder:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-or-service-key
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
```

### 3. Run Backend Engine
```bash
pip install -r requirements.txt
python -m backend.main
```

### 4. Run Frontend Dashboard
```bash
cd frontend
npm install
npm run dev
```
Open [http://localhost:5173/](http://localhost:5173/) in your web browser.
