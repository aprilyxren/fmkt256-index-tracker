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
Scheduler / GitHub Actions.

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

NEWS AUTOMATION: by default the script auto-writes the `news` column, but
ONLY using headlines whose claimed direction (e.g. "oil surges") is
CONFIRMED against that day's actual observed price/yield move -- if a
headline says oil rose but WTI actually fell that day, it's dropped, not
written. This avoids the earlier failure mode of templated guesses that
could contradict your own data or be too vague to count as "real news"
per the assignment rubric.

If nothing can be confirmed that way on a given day, `news` is left
blank and a review draft is printed instead -- because writing nothing
is safer than writing an unverified guess. You can then:
  - review with:  python market_indices.py --draft-news 2026-09-09
  - commit with:  python market_indices.py --add-news 2026-09-09 "final text"

Flags:
  (no flag)        fetch prices, auto-write confirmed news if available
  --no-auto-news   fetch prices, always leave news blank, print draft only
  --fetch-news     print all raw headlines (unfiltered) for browsing
  --draft-news [date]   reprint the draft for an existing row
  --add-news <date> "text"   manually set/overwrite the news column

IMPORTANT CAVEAT: "confirmed" here means direction-consistent with your
data, not verified as the TRUE cause of the move -- multiple things can
happen on the same day. Spot-check the auto-written notes periodically,
especially early on, rather than trusting them blindly for a graded
deliverable.
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
# a .env file (NEWSAPI_KEY=...), a GitHub Actions secret, etc.
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_indices.csv")

YF_TICKERS = {
    "dow": "^DJI",
    "sp500": "^GSPC",
    "us10y_yield_pct": "^TNX",
    "wti_crude": "CL=F",
}

STOOQ_TICKERS = {
    "dow": "^dji",
    "sp500": "^spx",
    "us10y_yield_pct": "10usy.b",
    "wti_crude": "cl.f",
}

CSV_FIELDS = ["date", "dow", "sp500", "us10y_yield_pct", "wti_crude", "news"]

# Which metric each headline "topic" maps to, for matching headline
# direction against actual observed price/yield direction.
TOPIC_TO_METRIC = {
    "oil": "wti_crude",
    "crude": "wti_crude",
    "wti": "wti_crude",
    "brent": "wti_crude",
    "yield": "us10y_yield_pct",
    "yields": "us10y_yield_pct",
    "treasury": "us10y_yield_pct",
    "10-year": "us10y_yield_pct",
    "10 year": "us10y_yield_pct",
    "stock": "sp500",
    "stocks": "sp500",
    "equity": "sp500",
    "equities": "sp500",
    "s&p": "sp500",
    "dow": "dow",
}

UP_WORDS = ["rise", "rises", "rising", "up", "higher", "climb", "climbs", "surge",
            "surges", "jump", "jumps", "rally", "rallies", "gain", "gains", "soar", "soars"]
DOWN_WORDS = ["fall", "falls", "falling", "down", "lower", "drop", "drops", "slide",
              "slides", "sink", "sinks", "sell-off", "selloff", "retreat", "retreats",
              "tumble", "tumbles", "slump", "slumps", "plunge", "plunges"]


# ---------------------------------------------------------------------------
# Price fetchers
# ---------------------------------------------------------------------------

def fetch_from_yfinance(ticker: str):
    try:
        hist = yf.Ticker(ticker).history(period="5d", interval="1d")
        if hist.empty:
            return None
        return float(hist["Close"].dropna().iloc[-1])
    except Exception as e:
        print(f"  [yfinance] failed for {ticker}: {e}", file=sys.stderr)
        return None


def fetch_from_stooq(stooq_symbol: str):
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
    if value is None:
        return None
    if value > 20:
        return round(value / 10, 3)
    return round(value, 3)


def fetch_field(name: str):
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


def fetch_all(auto_news: bool = True):
    print(f"Fetching market indices @ {datetime.now().isoformat(timespec='seconds')}")
    row = {"date": datetime.now().strftime("%Y-%m-%d")}
    for field in ["dow", "sp500", "us10y_yield_pct", "wti_crude"]:
        row[field] = fetch_field(field)
    row["news"] = ""

    if auto_news:
        prev_row = get_previous_row(row["date"])
        headlines = fetch_news_headlines()
        confirmed, _ = get_confirmed_matches(row, prev_row, headlines)
        auto_text = compose_auto_news(confirmed)
        if auto_text:
            row["news"] = auto_text
            print(f"  [auto-news] wrote confirmed note: {auto_text}")
        else:
            print("  [auto-news] no headline could be confirmed against today's actual "
                  "price moves -- leaving news blank. Run --draft-news to review candidates "
                  "and fill it in manually with --add-news.")
    return row


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

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


def get_row_for_date(date_str: str, path: str = OUTPUT_CSV):
    if not os.path.isfile(path):
        return None
    df = pd.read_csv(path, dtype=str)
    matches = df[df["date"] == date_str]
    if matches.empty:
        return None
    return matches.iloc[-1].to_dict()


def get_previous_row(date_str: str, path: str = OUTPUT_CSV):
    """Most recent row strictly before date_str, or None."""
    if not os.path.isfile(path):
        return None
    df = pd.read_csv(path, dtype=str)
    df = df[df["date"] < date_str].sort_values("date")
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


def add_news(date_str: str, news_text: str, path: str = OUTPUT_CSV):
    """
    Commit the final news note for a given date, e.g.:
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


# ---------------------------------------------------------------------------
# News fetching
# ---------------------------------------------------------------------------

def fetch_news_headlines(max_headlines: int = 15, retries: int = 2):
    """
    Pull recent market-moving headlines. Tries NewsAPI first (needs
    NEWSAPI_KEY), retries on transient errors/rate limits, falls back to
    Yahoo Finance's free RSS feed if NewsAPI is unavailable.
    Returns a list of dicts: {"title", "source", "url"}.
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


VAGUE_PHRASES = [
    "increased investor demand", "investor demand", "market sentiment",
    "market optimism", "risk appetite", "broad-based gains", "broader gains",
    "higher demand", "up today owing to", "gains as investors", "added demand",
    "investors are bullish", "investors are optimistic",
]


def is_vague_news_headline(title: str) -> bool:
    text = title.lower()
    if any(phrase in text for phrase in VAGUE_PHRASES):
        return True
    if "investor" in text and "demand" in text:
        return True
    return False


def print_news_candidates():
    """
    CLI helper: print today's raw headline candidates, unfiltered, so you
    can eyeball everything available.
        python market_indices.py --fetch-news
    """
    headlines = fetch_news_headlines()
    if not headlines:
        print("No headlines retrieved from either source.")
        return
    print(f"\nFound {len(headlines)} candidate headlines:\n")
    for i, h in enumerate(headlines, 1):
        flag = "  [VAGUE -- avoid]" if is_vague_news_headline(h["title"]) else ""
        print(f"{i}. [{h['source']}] {h['title']}{flag}")
        print(f"   {h['url']}\n")


# ---------------------------------------------------------------------------
# Grounded draft summary: ties real headlines to real observed price moves
# ---------------------------------------------------------------------------

def compute_deltas(row: dict, prev_row: dict):
    """Return {metric: (delta, pct_or_bps, direction)} for the 4 tracked fields."""
    deltas = {}
    for metric in ["dow", "sp500", "us10y_yield_pct", "wti_crude"]:
        try:
            cur = float(row.get(metric))
            prev = float(prev_row.get(metric)) if prev_row else None
        except (TypeError, ValueError):
            cur, prev = None, None
        if cur is None or prev is None:
            deltas[metric] = None
            continue
        diff = cur - prev
        direction = "up" if diff > 0 else ("down" if diff < 0 else "flat")
        if metric == "us10y_yield_pct":
            change_desc = f"{diff * 100:+.0f}bp to {cur:.2f}%"
        else:
            pct = (diff / prev) * 100 if prev else 0
            change_desc = f"{pct:+.2f}% to {cur:,.2f}"
        deltas[metric] = (diff, change_desc, direction)
    return deltas


def headline_topics_and_direction(title: str):
    """Return list of (metric, claimed_direction) implied by the headline text."""
    text = title.lower()
    matches = []
    for topic, metric in TOPIC_TO_METRIC.items():
        if topic in text:
            claimed = None
            if any(w in text for w in UP_WORDS):
                claimed = "up"
            elif any(w in text for w in DOWN_WORDS):
                claimed = "down"
            matches.append((metric, claimed))
    return matches


def get_confirmed_matches(row: dict, prev_row: dict, headlines: list):
    """
    Return (confirmed, unverified) lists of (headline, metric, delta_tuple).
    confirmed = headline's claimed direction matches the ACTUAL observed
    direction for the metric it references that day (the only tier safe
    enough to auto-write into the CSV).
    unverified = topically relevant but headline made no directional claim
    to check, or no prior row exists to compute a delta against.
    Contradicting headlines are dropped entirely, not returned.
    """
    if not headlines:
        return [], []

    deltas = compute_deltas(row, prev_row) if prev_row else {m: None for m in CSV_FIELDS[1:5]}

    confirmed = []
    unverified = []
    seen = set()

    for h in headlines:
        title = h.get("title", "")
        if not title or is_vague_news_headline(title) or title in seen:
            continue
        topics = headline_topics_and_direction(title)
        if not topics:
            continue
        for metric, claimed in topics:
            d = deltas.get(metric)
            if d is None:
                continue
            actual_dir = d[2]
            if claimed is None:
                unverified.append((h, metric, d))
                seen.add(title)
            elif claimed == actual_dir:
                confirmed.append((h, metric, d))
                seen.add(title)
            # contradicting claims are silently dropped

    return confirmed, unverified


def compose_auto_news(confirmed: list) -> str:
    """
    Build a citable, grounded news sentence from CONFIRMED matches only
    (headline direction verified against actual same-day data movement).
    Returns "" if there's nothing safe to auto-write.
    """
    if not confirmed:
        return ""
    parts = []
    seen_titles = set()
    for h, metric, d in confirmed[:3]:   # cap at 3 to keep it readable
        if h["title"] in seen_titles:
            continue
        seen_titles.add(h["title"])
        parts.append(f'"{h["title"]}" ({h["source"]}) -- {metric} moved {d[1]}')
    return "Per " + "; ".join(parts) + "."


def build_draft_summary(row: dict, prev_row: dict, headlines: list):
    """
    Build a draft news note for display purposes (used by --draft-news and
    --fetch-news review, and as the fallback message when auto-write finds
    nothing confirmed).
    """
    confirmed, unverified = get_confirmed_matches(row, prev_row, headlines)

    lines = []
    if not prev_row:
        lines.append("[DRAFT] No prior-day row found for comparison -- deltas unavailable "
                      "(this may be the first logged row). Headlines below are unverified against direction.")

    seen_titles = set()
    for h, metric, d in confirmed:
        if h["title"] in seen_titles:
            continue
        seen_titles.add(h["title"])
        lines.append(
            f'- CONFIRMED match: "{h["title"]}" ({h["source"]}) -- '
            f'{metric} moved {d[1]}, consistent with this headline. {h["url"]}'
        )

    if not confirmed:
        for h, metric, d in unverified[:5]:
            if h["title"] in seen_titles:
                continue
            seen_titles.add(h["title"])
            lines.append(
                f'- Possibly relevant (direction not stated in headline, verify manually): '
                f'"{h["title"]}" ({h["source"]}) -- {metric} moved {d[1]}. {h["url"]}'
            )

    if not lines or (len(lines) == 1 and not prev_row):
        lines.append("- No headline could be matched to a tracked metric's actual direction today. "
                      "Run --fetch-news to see all raw headlines and write the note manually.")

    header = "[DRAFT news summary -- review before committing with --add-news]"
    return header + "\n" + "\n".join(lines)


def print_draft_news(date_str: str = None):
    """
    python market_indices.py --draft-news [YYYY-MM-DD]
    Regenerates the draft for an existing row without re-fetching prices.
    Defaults to today.
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    row = get_row_for_date(date_str)
    if row is None:
        print(f"No row found for {date_str} in {OUTPUT_CSV}. Run the script first to log prices.")
        return
    prev_row = get_previous_row(date_str)
    headlines = fetch_news_headlines()
    draft = build_draft_summary(row, prev_row, headlines)
    print(f"\n{draft}\n")
    print(f'To commit: python market_indices.py --add-news {date_str} "your final text"')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--add-news":
        add_news(sys.argv[2], " ".join(sys.argv[3:]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "--fetch-news":
        print_news_candidates()
    elif len(sys.argv) >= 2 and sys.argv[1] == "--draft-news":
        date_arg = sys.argv[2] if len(sys.argv) >= 3 else None
        print_draft_news(date_arg)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--no-auto-news":
        row = fetch_all(auto_news=False)
        append_row(row)
        prev_row = get_previous_row(row["date"])
        headlines = fetch_news_headlines()
        draft = build_draft_summary(row, prev_row, headlines)
        print(f"\n{draft}\n")
        print(f'To commit: python market_indices.py --add-news {row["date"]} "your final text"')
    else:
        # Default: fetch prices AND auto-write a confirmed news note if one
        # is available. If nothing could be confirmed against today's real
        # data, news stays blank and a review draft is printed instead.
        row = fetch_all(auto_news=True)
        append_row(row)
        if not row["news"]:
            prev_row = get_previous_row(row["date"])
            headlines = fetch_news_headlines()
            draft = build_draft_summary(row, prev_row, headlines)
            print(f"\n{draft}\n")
            print(f'To commit: python market_indices.py --add-news {row["date"]} "your final text"')

# ---------------------------------------------------------------------------
# Suggested cron entry (runs weekdays at 5:15pm ET -- adjust TZ as needed):
#   15 17 * * 1-5 /usr/bin/python3 /path/to/market_indices.py >> /path/to/market_indices.log 2>&1
#
# Since you're running this locally (VS Code / macOS), check your machine's
# timezone first with: date
# If it already shows Eastern Time, the "17" (5pm) in the cron line above
# is correct as-is. If your Mac is set to a different timezone, adjust the
# hour accordingly, or just set the schedule using local wall-clock time
# (cron on macOS uses whatever timezone the machine is set to, not UTC).
#
# Note: cron only fires while your Mac is awake and logged in. If you want
# this to run reliably even when the laptop is closed/asleep, that's the
# main advantage of the GitHub Actions workflow you already set up (it
# runs on GitHub's servers regardless of your machine's state) -- you can
# use cron locally for testing and GitHub Actions for the reliable
# scheduled version.
# ---------------------------------------------------------------------------