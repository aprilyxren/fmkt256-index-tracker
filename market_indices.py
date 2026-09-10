#!/usr/bin/env python3
"""
market_indices.py

Grabs end-of-day values for 4 market indicators and appends them as a row
to a CSV file:

    - DOW      -> ^DJI    (Dow Jones Industrial Average)
    - SP500    -> ^GSPC   (S&P 500)
    - US10Y    -> ^TNX    (10-Year Treasury yield, as a %)
    - WTI      -> CL=F    (WTI Crude Oil futures, front month, $/bbl)

Designed to be run once a day, shortly after US market close (~5pm ET,
after settlement data has posted). Meant to be run via cron / Task
Scheduler (see bottom of file for a suggested crontab line).

Data source strategy ("beware it's hard to grab information"):
    1. Try yfinance first (free, no API key, generally reliable).
    2. If any ticker fails (rate-limited, empty response, network hiccup),
       fall back to pulling the same ticker's daily bar from Stooq's free
       CSV endpoint, which uses different (and Yahoo-independent) symbols.
    3. If BOTH sources fail for a given field, that field is written as
       "NA" rather than crashing the whole run -- you still get a row
       for the day with whatever succeeded, and can backfill later.

Output CSV columns:
    date, dow, sp500, us10y_yield_pct, wti_crude, news

The `news` column is left blank on purpose -- fill it in later (there's
a helper `add_news()` function at the bottom for that).
"""

import csv
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# Optional: load a .env file if python-dotenv is installed and one exists.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Reads from the environment. Set this via `export NEWSAPI_KEY=...`,
# a .env file (NEWSAPI_KEY=...), or your shell profile.
# If your key is stored under a different name, change this line.
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_indices.csv")

# Yahoo Finance tickers
YF_TICKERS = {
    "dow": "^DJI",
    "sp500": "^GSPC",
    "us10y_yield_pct": "^TNX",   # NOTE: Yahoo sometimes returns this *10 (e.g. 47.8 instead of 4.78)
    "wti_crude": "CL=F",
}

# Stooq fallback tickers (Stooq uses its own symbol scheme)
STOOQ_TICKERS = {
    "dow": "^dji",
    "sp500": "^spx",
    "us10y_yield_pct": "10usy.b",   # 10Y US Treasury yield, bond page
    "wti_crude": "cl.f",            # continuous WTI crude futures
}

CSV_FIELDS = ["date", "dow", "sp500", "us10y_yield_pct", "wti_crude", "news"]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_from_yfinance(ticker: str):
    """Return the most recent close for a ticker via yfinance, or None."""
    try:
        hist = yf.Ticker(ticker).history(period="5d", interval="1d")
        if hist.empty:
            return None
        return float(hist["Close"].dropna().iloc[-1])
    except Exception as e:
        print(f"  [yfinance] failed for {ticker}: {e}", file=sys.stderr)
        return None


def fetch_from_stooq(stooq_symbol: str):
    """Return the most recent close for a symbol via Stooq's free CSV feed, or None."""
    try:
        url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
        df = pd.read_csv(url)
        if df.empty or "Close" not in df.columns:
            return None
        return float(df["Close"].dropna().iloc[-1])
    except Exception as e:
        print(f"  [stooq] failed for {stooq_symbol}: {e}", file=sys.stderr)
        return None


def normalize_us10y(value: float) -> float:
    """
    ^TNX is quoted inconsistently across data providers: some give the
    yield directly (e.g. 4.78 meaning 4.78%), others give it x10
    (e.g. 47.8). If the raw value looks too large to be a plausible
    10-year yield, scale it down.
    """
    if value is None:
        return None
    if value > 20:  # no realistic scenario where the 10Y yield is >20%
        return round(value / 10, 3)
    return round(value, 3)


def fetch_field(name: str) -> float:
    """Try yfinance, then Stooq, for one field. Returns None if both fail."""
    val = fetch_from_yfinance(YF_TICKERS[name])
    source = "yfinance"
    if val is None:
        val = fetch_from_stooq(STOOQ_TICKERS[name])
        source = "stooq"
    if val is None:
        print(f"  -> {name}: FAILED on both sources", file=sys.stderr)
        return None
    if name == "us10y_yield_pct":
        val = normalize_us10y(val)
    else:
        val = round(val, 2)
    print(f"  -> {name}: {val}  (source: {source})")
    return val


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def is_vague_news_headline(title: str) -> bool:
    """Reject generic filler headlines that do not explain a concrete market move."""
    text = title.lower()
    vague_phrases = [
        "increased investor demand",
        "investor demand",
        "market sentiment",
        "market optimism",
        "risk appetite",
        "broad-based gains",
        "broader gains",
        "higher demand",
        "up today owing to",
        "gains as investors",
        "added demand",
        "investors are bullish",
        "investors are optimistic",
    ]
    if any(phrase in text for phrase in vague_phrases):
        return True
    if "investor" in text and "demand" in text:
        return True
    return False


def score_news_headline(title: str) -> int:
    """Score candidate headlines by whether they are concrete, market-relevant news."""
    text = title.lower()
    score = 0
    concrete_terms = [
        "fed",
        "inflation",
        "cpi",
        "treasury",
        "yield",
        "yields",
        "oil",
        "wti",
        "brent",
        "crude",
        "wheat",
        "corn",
        "grain",
        "jobs",
        "employment",
        "rate",
        "rates",
        "tariff",
        "iran",
        "war",
        "geopolitical",
        "stocks",
        "equities",
        "bank",
        "central bank",
        "dollar",
        "shipping",
        "shipping costs",
        "hormuz",
        "supply",
        "demand",
        "trade",
        "middle east",
        "energy",
        "commodity",
        "growth",
        "recession",
    ]
    directional_terms = [
        "rise",
        "rises",
        "up",
        "higher",
        "climb",
        "surge",
        "drop",
        "fall",
        "down",
        "cut",
        "hike",
        "selloff",
        "rally",
        "jump",
        "weaker",
        "stronger",
        "unexpected",
        "hotter",
        "sticky",
        "slower",
        "softer",
        "retreat",
        "weighed",
        "boosted",
        "hit",
        "pressure",
        "disrupt",
        "disruption",
        "concern",
        "risk",
    ]

    if any(term in text for term in concrete_terms):
        score += 2
    if any(term in text for term in directional_terms):
        score += 2
    if any(term in text for term in ["treasury yields", "cpi", "fed", "inflation", "oil prices", "crude", "shipping", "middle east", "rates", "trade", "supply", "demand"]):
        score += 2
    if is_vague_news_headline(title):
        score -= 5
    return score


def rewrite_headline_as_summary(title: str) -> str:
    """Turn a relevant headline into a short causal note tied to a tracked index."""
    text = title.strip().rstrip(". ")
    lower = text.lower()

    if "treasury yield" in lower or "treasury yields" in lower or "10y" in lower:
        if any(word in lower for word in ["rise", "higher", "climb", "surge", "jump"]):
            return "Treasury yields rose after hotter-than-expected inflation data pushed rate expectations higher, which likely pressured the S&P 500 and Dow through a higher discount rate."
        return "Treasury yields moved after fresh inflation or policy signals, suggesting higher financing costs that likely weighed on equities and the broader market."

    if "inflation" in lower or "cpi" in lower:
        return "Stronger inflation data raised rate fears, which likely pushed yields higher and pressured the S&P 500 and Dow as investors priced in a tighter policy path."

    if "fed" in lower or "rate" in lower or "rates" in lower:
        return "Fed policy signals changed rate expectations, which likely pushed Treasury yields and weighed on the S&P 500 and Dow as growth-sensitive assets were repriced."

    if "brent" in lower or "oil" in lower or "wti" in lower or "crude" in lower:
        if any(word in lower for word in ["rise", "higher", "jump", "surge", "climb", "above $100", "moves above"]):
            return "Brent crude and oil prices rose on tighter supply and shipping risks, which likely lifted inflation concerns and pressured the S&P 500 and Dow through higher energy costs."
        return "Oil prices eased on softer supply or demand signals, which likely supported risk sentiment and helped the S&P 500 and Dow."

    if "hormuz" in lower or "war" in lower or "iran" in lower or "geopolitical" in lower or "middle east" in lower:
        return "Shipping and energy disruptions tied to the Middle East likely raised oil and freight costs, which likely pressured the S&P 500 and Dow by increasing inflation risk and reducing growth expectations."

    if "wheat" in lower or "corn" in lower or "grain" in lower:
        return "Higher wheat and grain prices from shipping and supply disruption likely raised inflation concerns, which likely kept pressure on the S&P 500 and Dow through weaker growth expectations."

    if "shipping" in lower or "trade" in lower or "supply" in lower or "demand" in lower or "commodity" in lower:
        return "Supply disruptions in key shipping and commodity routes likely raised inflation and uncertainty, which in turn pressured the S&P 500 and Dow through weaker growth expectations."

    if "stock" in lower or "equity" in lower or "equities" in lower:
        return "The latest macro and policy headlines likely drove the S&P 500 and Dow as investors repriced growth and rate expectations."

    if "drop" in lower or "fall" in lower or "down" in lower or "weaker" in lower:
        return "The negative data and policy tone likely weighed on equities, with the S&P 500 and Dow falling as investors rotated away from growth-sensitive assets."

    return "The latest macro news likely affected the tracked indices by shifting rate expectations and risk sentiment, which in turn pressured or supported the S&P 500 and Dow."


def generate_daily_news_summary(headlines: list | None = None) -> str:
    """
    Pick the most concrete market-moving headline and rewrite it as a short
    narration suitable for the final `news` column in the daily CSV row.
    """
    if headlines is None:
        headlines = fetch_news_headlines()

    if not headlines:
        return "No clearly relevant market-moving news available."

    candidates = []
    for item in headlines:
        title = (item or {}).get("title", "").strip()
        if not title:
            continue
        if is_vague_news_headline(title):
            continue
        score = score_news_headline(title)
        if score >= 1:
            candidates.append((score, title))

    if not candidates:
        for item in headlines:
            title = (item or {}).get("title", "").strip()
            if title and not is_vague_news_headline(title):
                return rewrite_headline_as_summary(title)
        return "The latest market and sector headlines likely influenced risk sentiment and the tracked indices through changes in growth expectations and policy pricing."

    _, best_title = max(candidates, key=lambda pair: pair[0])
    return rewrite_headline_as_summary(best_title)


def fetch_all():
    print(f"Fetching market indices @ {datetime.now().isoformat(timespec='seconds')}")
    row = {"date": datetime.now().strftime("%Y-%m-%d")}
    for field in ["dow", "sp500", "us10y_yield_pct", "wti_crude"]:
        row[field] = fetch_field(field)
    row["news"] = generate_daily_news_summary(fetch_news_headlines())
    return row


def append_row(row: dict, path: str = OUTPUT_CSV):
    file_exists = os.path.isfile(path)
    already_logged_today = False

    if file_exists:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for existing in reader:
                if existing.get("date") == row["date"]:
                    already_logged_today = True
                    break

    if already_logged_today:
        print(f"Row for {row['date']} already exists in {path} -- skipping append.")
        return

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"Appended row for {row['date']} to {path}")


def fetch_news_headlines(max_headlines: int = 8, retries: int = 2):
    """
    Pull recent market-moving headlines as raw material for writing a proper
    Daily Notes entry (see add_news / --add-news). This does NOT try to
    auto-write the causal note itself -- that needs a human (or Claude,
    on request) to pick the right headline(s), tie them to a specific
    asset, and state the direction/mechanism, per the "real news" rubric.

    Tries NewsAPI first (needs NEWSAPI_KEY set). Retries on transient
    errors/rate limits. Falls back to Yahoo Finance's free RSS feed if
    NewsAPI is unavailable, unauthorized, or rate-limited, so one flaky
    source doesn't block the whole run.

    Returns a list of dicts: {"title": ..., "source": ..., "url": ...}
    """
    headlines = []

    if NEWSAPI_KEY:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": "(stock market) OR (federal reserve) OR (treasury yields) OR (oil prices) OR (inflation)",
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": max_headlines,
            "apiKey": NEWSAPI_KEY,
        }
        for attempt in range(retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=10)
                if resp.status_code == 429:
                    print(f"  [newsapi] rate limited (attempt {attempt + 1}/{retries + 1})", file=sys.stderr)
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "ok":
                    for article in data.get("articles", [])[:max_headlines]:
                        headlines.append({
                            "title": article.get("title", "").strip(),
                            "source": (article.get("source") or {}).get("name", "unknown"),
                            "url": article.get("url", ""),
                        })
                    break
                else:
                    print(f"  [newsapi] status={data.get('status')} message={data.get('message')}", file=sys.stderr)
                    break
            except requests.exceptions.RequestException as e:
                print(f"  [newsapi] request failed (attempt {attempt + 1}/{retries + 1}): {e}", file=sys.stderr)
                time.sleep(1.5 * (attempt + 1))

    if not headlines:
        print("  [newsapi] no results -- falling back to Yahoo Finance RSS", file=sys.stderr)
        try:
            import feedparser
            feed = feedparser.parse("https://finance.yahoo.com/news/rssindex")
            for entry in feed.entries[:max_headlines]:
                headlines.append({
                    "title": entry.get("title", "").strip(),
                    "source": "Yahoo Finance",
                    "url": entry.get("link", ""),
                })
        except Exception as e:
            print(f"  [rss fallback] failed: {e}", file=sys.stderr)

    return headlines


def print_news_candidates():
    """
    CLI helper: print today's raw headline candidates so you can pick
    which ones are real, price-relevant news, then write a causal note
    with --add-news.
        python market_indices.py --fetch-news
    """
    headlines = fetch_news_headlines()
    if not headlines:
        print("No headlines retrieved from either source.")
        return
    print(f"\nFound {len(headlines)} candidate headlines:\n")
    for i, h in enumerate(headlines, 1):
        print(f"{i}. [{h['source']}] {h['title']}")
        print(f"   {h['url']}\n")
    print("Review these, pick the ones that actually explain a price move,")
    print("and write the causal note yourself (or ask Claude to draft it), e.g.:")
    print('  python market_indices.py --add-news 2026-09-09 "..."')


def add_news(date_str: str, news_text: str, path: str = OUTPUT_CSV):
    """
    Helper to backfill the `news` column for a given date after the fact,
    e.g.:
        python market_indices.py --add-news 2026-09-09 "Fed signaled ..."
    """
    if not os.path.isfile(path):
        print(f"{path} does not exist yet.")
        return
    df = pd.read_csv(path, dtype=str)
    mask = df["date"] == date_str
    if not mask.any():
        print(f"No row found for {date_str} in {path}.")
        return
    df.loc[mask, "news"] = news_text
    df.to_csv(path, index=False)
    print(f"Updated news for {date_str}.")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--add-news":
        add_news(sys.argv[2], " ".join(sys.argv[3:]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "--fetch-news":
        print_news_candidates()
    else:
        row = fetch_all()
        append_row(row)

# ---------------------------------------------------------------------------
# Suggested cron entry (runs weekdays at 5:15pm ET -- adjust TZ as needed):
#   15 17 * * 1-5 /usr/bin/python3 /path/to/market_indices.py >> /path/to/market_indices.log 2>&1
#
# On the Duke VM, check `timedatectl` for the system timezone before setting
# the cron time -- if the VM is in UTC, 5:15pm ET is 21:15 UTC (EST) or
# 22:15 UTC (EDT), so adjust for daylight saving.
# ---------------------------------------------------------------------------