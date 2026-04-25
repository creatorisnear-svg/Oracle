# TradeSignal AI — Setup Guide

## Run on Your Computer (MSI GE76)

### Prerequisites
1. **Python 3.11+** — https://python.org/downloads
2. **Node.js 20+** — https://nodejs.org
3. **pnpm** — Run: `npm install -g pnpm`

### Step 1 — Install Python dependencies
```bash
cd artifacts/api-server/trading
pip install fastapi uvicorn yfinance pandas numpy ta
```

### Step 2 — Install Node dependencies
```bash
# from repo root
pnpm install
```

### Step 3 — Start both servers (two terminals)

**Terminal 1 — Backend API (Port 8080):**
```bash
cd artifacts/api-server/trading
python server.py
```

**Terminal 2 — Dashboard UI (Port 8081):**
```bash
cd artifacts/mockup-sandbox
pnpm dev --port 8081
```

### Step 4 — Open in browser
```
http://localhost:8081/__mockup/
```

The trading dashboard will appear. Click any symbol or type a ticker and hit **Analyze**.

---

## Push to GitHub

### From Replit (first time):
1. Go to https://github.com/new and create a new empty repo (no README)
2. In the Replit shell, run:
```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### On your MSI laptop (after cloning):
```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```
Then follow the "Run on Your Computer" steps above.

---

## Auto-Updates

The dashboard auto-updates **every 3 seconds** via WebSocket:
- Live price streams to the price ticker in the top-right corner
- News feed updates with each analysis
- Agents vote one-by-one in real-time as the WebSocket streams
- The **prediction line** appears on the chart automatically after analysis

## Signals Explained

| Signal | Meaning | When It Fires |
|--------|---------|---------------|
| ⬆ CALL | Buy a CALL option — price going UP | 6+ of 7 agents agree bullish |
| ⬇ PUT  | Buy a PUT option — price going DOWN | 6+ of 7 agents agree bearish |
| ⏸ HOLD | No position — wait for setup | Fewer than 6 agents agree |

**Strike Hint** = Suggested option strike price (near-the-money)
**Expiry Hint** = Suggested days-to-expiration (7-14 DTE = weeklies)
**Target** = Entry + 3×ATR (profit-taking level)
**Stop** = Entry - 2×ATR (max risk / close position here)
**R/R** = Risk/Reward ratio (anything above 1.5:1 is acceptable)

## Indicators Used

| Indicator | Agent | What it measures |
|-----------|-------|-----------------|
| EMA 9/21/50 | Technical | Trend direction across timeframes |
| MACD 12/26/9 | Technical | Momentum crossover |
| RSI 14 | Technical | Overbought/oversold |
| Bollinger Bands | Technical | Volatility squeeze/expansion |
| **SuperTrend** | Price Action | ATR-based trend regime |
| **VWAP** | Volume/Momentum | Institutional price anchor |
| Stochastic %K | Technical | Short-term oscillator |
| OBV slope | Volume | On-balance volume trend |
| Put/Call ratio | Options Flow | Real options market sentiment |
| Multi-TF Score | Technical | Price vs SMA20/50 trend score |
