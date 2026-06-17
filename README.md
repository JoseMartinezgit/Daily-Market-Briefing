# Daily Market Briefing

A local Bloomberg-lite dashboard that generates a comprehensive daily market snapshot — aggregating financial news, scoring sentiment, mapping affected stocks, and producing a formatted report. Runs entirely on your machine with no subscription fees.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure API keys (optional but recommended)
cp .env.example .env
# Edit .env with your keys (see "Getting API Keys" below)

# 3. Launch dashboard
python run.py
# Opens http://localhost:8000 automatically
```

On first launch the app fetches live data (takes ~30 seconds). Click **Refresh** anytime to re-pull.

---

## Run Modes

| Command | What it does |
|---------|-------------|
| `python run.py` | Launch web dashboard at localhost:8000 |
| `python run.py --port 8080` | Use a different port |
| `python run.py --no-browser` | Start server without auto-opening browser |
| `python run.py --report` | Print markdown report + save to `/reports/` |
| `python run.py --report --llm` | Same, using Anthropic API for better sentiment |
| `python run.py --schedule 08:00` | Auto-generate report at 8:00 AM CT daily |

**LAN access from phone:** The server binds to `0.0.0.0` by default. Open `http://YOUR_PC_IP:8000` on your phone when on the same WiFi.

---

## Getting API Keys

All keys are optional. The app degrades gracefully — if no keys are set, it uses RSS feeds only (still pulls a solid news feed).

### Free-Tier Keys (10 minutes to set up)

| Service | What it adds | How to get |
|---------|-------------|------------|
| **Finnhub** | Financial news, 60 calls/min | [finnhub.io/register](https://finnhub.io/register) |
| **NewsAPI** | Business news headlines, 100 req/day | [newsapi.org/register](https://newsapi.org/register) |
| **Alpha Vantage** | News + sentiment, 25 req/day | [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key) |
| **Marketaux** | Financial news + entity tagging, 100 req/day | [marketaux.com/register](https://www.marketaux.com/register) |
| **FRED** | Economic release calendar (exact dates) | [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) |

### For LLM Mode (`--llm`)

| Service | What it adds |
|---------|-------------|
| **Anthropic** | Higher-quality sentiment classification and impact reasoning | [console.anthropic.com](https://console.anthropic.com/) |

Add keys to your `.env` file:
```
FINNHUB_API_KEY=your_key_here
NEWS_API_KEY=your_key_here
ALPHA_VANTAGE_KEY=your_key_here
MARKETAUX_API_KEY=your_key_here
FRED_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here    # only needed for --llm mode
```

---

## Configuration

Edit `config.yaml` to customise:

```yaml
timezone: America/Chicago       # Display timezone (CT by default)

keyword_alerts:                 # Stories matching these are pinned to top
  - tariff
  - NVDA
  - rate cut

watchlist:                      # Always shown in the watchlist panel
  - AAPL
  - MSFT
  - NVDA

sources:                        # Toggle individual sources on/off
  rss_reuters: true
  api_finnhub: true             # Only active if key is set

sentiment_weights:              # Tune the day verdict algorithm
  market_indices: 0.40
  vix: 0.20
  news_sentiment: 0.30
  breadth: 0.10
```

The sector knowledge map (`data/sector_map.json`) lists which stocks/ETFs are affected by each macro driver (rate hikes, tariffs, oil moves, etc.). You can extend it with your own ticker relationships.

---

## Project Structure

```
dailyRep/
├── run.py                      # Entry point
├── config.yaml                 # Your configuration
├── .env                        # API keys (you create this)
├── .env.example                # Template
├── requirements.txt
├── README.md
├── reports/                    # Auto-saved daily markdown reports
├── db/                         # SQLite database (auto-created)
└── src/
    ├── main.py                 # FastAPI app + refresh pipeline
    ├── database.py             # SQLAlchemy models
    ├── cache.py                # In-memory TTL cache
    ├── config.py               # Config loader
    ├── report.py               # Markdown report generator
    ├── scheduler.py            # Scheduled report runner
    ├── aggregators/
    │   ├── rss_feeds.py        # Reuters, CNBC, MarketWatch, Yahoo, Fed, SEC
    │   ├── news_api.py         # Finnhub, NewsAPI, Alpha Vantage, Marketaux
    │   ├── market_data.py      # yfinance: indices, ETFs, VIX, watchlist prices
    │   └── events.py           # Economic calendar + earnings + FOMC + OPEX
    ├── analysis/
    │   ├── sentiment.py        # Keyword lexicon + VADER + optional LLM
    │   ├── ticker_mapping.py   # Ticker extraction + sector impact mapping
    │   └── verdict.py          # Day verdict (BULLISH/BEARISH/VOLATILE/MIXED)
    ├── static/
    │   └── index.html          # Dark-mode SPA dashboard
    └── data/
        ├── sector_map.json     # Macro driver → sector/ticker impacts
        └── finance_lexicon.json  # Bullish/bearish keyword weights
```

---

## Dashboard Features

- **Verdict banner** — BULLISH 🟢 / BEARISH 🔴 / VOLATILE 🟡 / MIXED ⚪ with confidence score and plain-English justification
- **Market snapshot** — S&P 500, Nasdaq, Dow, Russell 2000, VIX, Gold, Oil, BTC, 10Y yield
- **Sector heatmap** — all 11 GICS sectors colored by today's % change
- **News feed** — filterable by Macro / Political / Deals / Earnings / Sector; each card shows sentiment, impact, and linked tickers
- **Keyword alerts** — stories matching your configured keywords are pinned with a purple highlight
- **Event timeline** — upcoming CPI, NFP, jobless claims, FOMC, earnings, OPEX — all in Central Time
- **Watchlist** — top mentioned tickers across today's news with price data
- **Earnings reactions** — recent earnings surprises with EPS vs estimate and opening price reaction
- **Risks to watch** — 2-3 auto-generated warnings
- **History page** — charts of daily verdict, VIX, and S&P 500 change over time
- **Reports page** — browse and export past reports as markdown
- **Pre-market mode** — automatically detected before 8:30 AM CT; emphasizes futures and day-ahead calendar

---

## Sentiment Algorithm

**Mode A (default):** keyword/rule-based finance lexicon → combined with VADER sentiment → mapped to bullish/bearish/neutral + impact level (High/Medium/Low).

**Mode B (`--llm`):** routes headlines through `claude-haiku-4-5-20251001` via the Anthropic API in batches of 20 for higher-quality classification. Degrades gracefully to Mode A if no API key is set. Typical cost: <$0.01 per daily run.

---

## Day Verdict Algorithm

Weighted blend of four signals:

| Signal | Default Weight |
|--------|---------------|
| Index % changes (S&P 40%, Nasdaq 30%, Dow 20%, Russell 10%) | 40% |
| VIX level + rate of change | 20% |
| Aggregate news sentiment | 30% |
| Sector ETF breadth (% advancing vs declining) | 10% |

**Volatile override:** if VIX ≥ 25 AND S&P move ≥ 1.5%, verdict becomes VOLATILE regardless of direction.

Weights are configurable in `config.yaml` under `sentiment_weights`.

---

## Reports

Reports are saved to `/reports/YYYY-MM-DD.md` automatically on each refresh. They include:
- Verdict + justification
- Market snapshot table
- Sector heatmap
- Top 5 stories with affected tickers
- Full categorized news feed
- Event timeline
- Watchlist with prices
- Risks to watch

Download any report from the **Reports** tab in the UI, or access directly at `http://localhost:8000/api/export/YYYY-MM-DD`.

---

## Disclaimer

This tool aggregates public data for informational purposes only. It does not constitute financial advice. Always conduct your own research before making investment decisions. Past sentiment patterns do not predict future market outcomes.
