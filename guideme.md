# Complete Setup and Operations Guide (guideme.md)

This guide provides step-by-step instructions to get your Supabase database, Telegram notifications, Python backend, and React dashboard set up and running on Windows.

---

## 💻 Step 1: Database Setup (Supabase)

1. **Create a Supabase Account:**
   - Go to [Supabase](https://supabase.com/) and sign up or sign in.
   - Click **New Project** and name it (e.g., `Binary-Signal-Bot`).
   - Create a strong database password and choose your region.

2. **Execute the Database Setup Script:**
   - On the left sidebar, click the **SQL Editor** icon (looks like `>_`).
   - Click **New Query** to create a blank editor.
   - Open your local file [supabase_setup.sql](./supabase_setup.sql) and copy the entire text.
   - Paste it into the Supabase SQL editor and click **Run** (at the bottom right).
   - This creates the `signals` table, adds sorting indexes, and enables **Realtime** notifications.

3. **Get your API Keys:**
   - Go to the **Project Settings** (gear icon on sidebar) -> **API**.
   - Copy the **Project URL**.
   - Copy the **anon / public** key (Project API Key).
   - Paste these details into your local `backend/.env` file.

---

## 🔔 Step 2: Telegram Setup (Alerts Channel)

To receive live signals on your phone/PC:

1. **Create a Telegram Bot:**
   - Open Telegram and search for `@BotFather`.
   - Send `/newbot` and follow the instructions.
   - BotFather will give you a **HTTP API Token** (e.g., `7384918239:AAH...`).
   - Copy this token and paste it as `TELEGRAM_BOT_TOKEN` in your `backend/.env`.

2. **Get your Chat ID:**
   - **For a Private Group/Channel:**
     - Create a new Telegram group or channel and add your bot as an **Administrator**.
     - Send a test message in the group/channel (e.g., "Hello Bot").
     - Open your web browser and go to: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
     - Look for the `"chat"` object in the JSON response. The `"id"` field is your Chat ID (it usually starts with a minus sign like `-100192837482`).
     - Copy this ID (with the minus sign) and paste it as `TELEGRAM_CHAT_ID` in your `backend/.env`.

---

## 🐍 Step 3: Running the Python Backend

1. **Install Python:**
   - Make sure Python 3.10+ is installed on your computer.
   - Ensure you check the box to **"Add Python to PATH"** during installation.

2. **Install Dependencies:**
   - Open your terminal/PowerShell inside the root directory and run:
     ```powershell
     pip install -r requirements.txt
     ```

3. **Configure Environment:**
   - Ensure the `.env` file inside the `backend/` folder contains your correct credentials:
     ```env
     SUPABASE_URL=https://jzjhdjstlokbgklmxlgv.supabase.co
     SUPABASE_KEY=sb_publishable_4neSllJ9YkupZ-VgpC9ZJQ_2OPCE0ha
     TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
     TELEGRAM_CHAT_ID=YOUR_TELEGRAM_CHAT_ID
     ```

4. **Start the Engine:**
   - Run the main script to start streaming live market prices:
     ```powershell
     python -m backend.main
     ```
   - *Note: On startup, it will run a database clean-up to purge data older than 7 days, and then subscribe to forex feeds.*

---

## 🎨 Step 4: Running the Frontend Dashboard

1. **Install Node.js:**
   - Download and install [Node.js (LTS version)](https://nodejs.org/).

2. **Install Packages:**
   - Open a separate terminal, navigate to the `frontend/` folder, and run:
     ```powershell
     cd frontend
     npm install
     ```

3. **Launch the Live Server:**
   - Run the development command:
     ```powershell
     npm run dev
     ```
   - Open the displayed local link (usually [http://localhost:5173/](http://localhost:5173/)) in your browser.

---

## ⚙️ Customizing Strategy Settings

You can customize parameters inside [backend/config.py](./backend/config.py):

* **Pairs Monitored:** Add or remove symbols from `MONITORED_PAIRS` (e.g., `"frxEURUSD"`, `"frxGBPUSD"`).
* **RSI thresholds:** Modify `RSI_OVERBOUGHT` (default 70) and `RSI_OVERSOLD` (default 30).
* **Stochastic thresholds:** Modify `STOCH_OVERBOUGHT` (default 80) and `STOCH_OVERSOLD` (default 20).
* **Volume Climax:** Adjust `VOLUME_CLIMAX_MULTIPLIER` (default 1.5). If an asset supports volume, the volume must exceed 1.5x of the 20-period moving average to trigger a trade.

---

## ☁️ Step 5: Live Cloud Deployment

To deploy your project to GitHub and make it run 24/7 in the cloud:

### 1. Push to GitHub
Create a GitHub repository, initialize git in your root project folder (`C:\Users\Waqas Zulfiqar\Desktop\Binary`), commit the files, and push them to GitHub.
*(Make sure that your `backend/.env` file is in your `.gitignore` so your private API keys are not exposed to the public!)*

### 2. Deploy Frontend on Vercel
1. Log in to [Vercel](https://vercel.com/) and click **Add New** -> **Project**.
2. Import your GitHub repository.
3. Under **Configure Project**:
   - **Framework Preset:** Select **Vite** or let it Auto-detect.
   - **Root Directory:** Edit this and select the **`frontend`** folder (very important!).
4. Add **Environment Variables** (expand the accordion):
   - Name: `VITE_SUPABASE_URL` | Value: `https://jzjhdjstlokbgklmxlgv.supabase.co`
   - Name: `VITE_SUPABASE_KEY` | Value: `sb_publishable_4neSllJ9YkupZ-VgpC9ZJQ_2OPCE0ha` (or your updated key)
5. Click **Deploy**. Vercel will build and give you a live HTTPS link!

### 3. Deploy Backend on Render (Free Tier Web Service)
Render requires web services to listen to a port. We have built a lightweight HTTP health check server into our backend to make it compatible with Render's **Free Tier Web Service**.
1. Log in to [Render](https://render.com/) and click **New** -> **Web Service**.
2. Connect your GitHub repository.
3. Configure the Web Service:
   - **Name:** `quantum-bot-backend`
   - **Language:** `Python`
   - **Root Directory:** Leave empty (run from root so it can resolve package imports).
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python -m backend.main`
4. Expand the **Advanced** section and click **Add Environment Variable** to add:
   - `SUPABASE_URL` = `https://jzjhdjstlokbgklmxlgv.supabase.co`
   - `SUPABASE_KEY` = `your-supabase-key`
   - `TELEGRAM_BOT_TOKEN` = `your-telegram-token`
   - `TELEGRAM_CHAT_ID` = `your-telegram-chat-id`
   - `PYTHONUNBUFFERED` = `1` (This ensures logs are printed instantly in the Render dashboard)
5. Click **Create Web Service**. Render will install requirements, start the health check web server, connect to Deriv WS, and run your bot 24/7!
