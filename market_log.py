#!/usr/bin/env python3
"""
market_log.py

Collects daily closing data for Dow, S&P 500, US 10-Yr Treasury yield, and
WTI Crude Oil from Stooq, plus a short market-moving headline from NewsAPI,
and appends/updates one row per day in a plain CSV log.

This deliberately does NOT touch Journal_template_2026.xlsx or any other
spreadsheet -- it just builds up a clean, portable log with the same column
labels, in the same order, as the journal's "Closing levels" sheet. When
you're ready, open the CSV (in Excel, Numbers, Google Sheets, anything) and
copy the values across into the actual journal.

Requirements:
    pip install requests

Usage:
    python market_log.py --csv market_log.csv --newsapi-key YOUR_KEY
    python market_log.py --csv market_log.csv --newsapi-key YOUR_KEY --date 2026-09-08
    python market_log.py --csv market_log.csv --newsapi-key YOUR_KEY --dry-run
"""

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

import requests

# Stooq symbols. Indices use a ^ prefix; WTI uses the CASH commodity symbol
# (.C) rather than the futures symbol (.F) because Stooq only exposes CSV
# history downloads for the cash series, not futures contracts.
STOOQ_SYMBOLS = {
    "DOW": "^dji",
    "S&P": "^spx",
    "US 10 YR": "10yusy.b",
    "WTI Crude Oil": "cl.c",
}

STOOQ_URL_TEMPLATE = "https://stooq.com/q/d/l/?s={symbol}&i=d"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

NEWSAPI_URL = "https://newsapi.org/v2/everything"

NEWS_COLUMN = "Short DAILY NEWS ITEM(S) that affected one or more of today's prices"

FIELDNAMES = ["DATE", "DOW", "S&P", "US 10 YR (%)", "WTI Crude Oil", NEWS_COLUMN]


# ---------------------------------------------------------------------------
# Price data (Stooq)
# ---------------------------------------------------------------------------

def fetch_stooq_close(name: str, symbol: str, target_day: date):
    """
    Download the daily CSV history for `symbol` and return the Close value
    for target_day specifically (not just "the latest row"), or None if
    that day's close isn't published yet or the request fails.
    """
    url = STOOQ_URL_TEMPLATE.format(symbol=symbol)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    # utf-8-sig strips a leading UTF-8 BOM if Stooq's response has one
    # (which otherwise turns the first header cell into "\ufeffDate" and
    # breaks the row["Date"] lookup below); it's a no-op if there's no BOM.
    text = resp.content.decode("utf-8-sig").strip()
    if not text or "," not in text:
        raise RuntimeError(f"Unexpected empty/invalid response for '{name}' ({symbol}) at {url}")

    lines = text.splitlines()
    header = lines[0].split(",")
    last_row = lines[-1].split(",")
    row = dict(zip(header, last_row))

    if "Date" not in row:
        # Stooq didn't give us the CSV shape we expected -- could be a rate
        # limit message, a block page, or a changed format. Print a chunk
        # of the actual raw response so the next run's log tells us exactly
        # what came back, instead of just "'Date'" with no context.
        raise RuntimeError(
            f"Response for '{name}' ({symbol}) didn't look like the expected "
            f"CSV (no 'Date' column). Raw response started with: {text[:300]!r}"
        )

    row_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
    close = float(row["Close"])

    if row_date != target_day:
        print(
            f"[warn] '{name}' latest available close is for {row_date}, "
            f"not {target_day} yet — leaving it blank. (Stooq may just need "
            f"more time to publish today's close; try running again later.)",
            file=sys.stderr,
        )
        return None

    return close


def scrape_all_series(target_day: date) -> dict:
    values = {}
    for name, symbol in STOOQ_SYMBOLS.items():
        try:
            values[name] = fetch_stooq_close(name, symbol, target_day)
        except Exception as exc:
            print(f"[warn] Failed to fetch {name}: {exc}", file=sys.stderr)
            values[name] = None
    return values


# ---------------------------------------------------------------------------
# News: one short sentence, matching the journal's sample-row style
# ---------------------------------------------------------------------------

def get_news_item(api_key: str, day: date) -> str:
    day_str = day.strftime("%Y-%m-%d")
    params = {
        "q": (
            "stock market OR Dow Jones OR S&P 500 OR Federal Reserve "
            "OR inflation OR treasury yields OR oil prices"
        ),
        "from": day_str,
        "to": day_str,
        "language": "en",
        "sortBy": "relevancy",
        "pageSize": 1,
        "apiKey": api_key,
    }
    try:
        resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        if not articles:
            return "(no matching headline found — fill in manually)"
        top = articles[0]
        source = (top.get("source") or {}).get("name", "")
        headline = top.get("title", "").strip()
        return f"{headline} ({source})" if source else headline
    except Exception as exc:
        print(f"[warn] News lookup failed: {exc}", file=sys.stderr)
        return "(news lookup failed — fill in manually)"


# ---------------------------------------------------------------------------
# CSV log: keyed by date, so re-running for the same day overwrites cleanly
# ---------------------------------------------------------------------------

def load_existing_rows(csv_path: Path) -> dict:
    rows = {}
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows[row["DATE"]] = row
    return rows


def save_rows(csv_path: Path, rows: dict) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for date_str in sorted(rows.keys()):
            writer.writerow(rows[date_str])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Log daily market data + a headline to a CSV file.")
    parser.add_argument("--csv", required=True, help="Path to the CSV log file (created if missing).")
    parser.add_argument("--newsapi-key", required=True, help="Your NewsAPI.org API key.")
    parser.add_argument("--date", help="Target date as YYYY-MM-DD (default: today).")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't save.")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    target_day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    if target_day.weekday() >= 5:
        print(f"{target_day.isoformat()} is a weekend — markets are closed, nothing to do.")
        return

    print("Fetching prices from Stooq...")
    values = scrape_all_series(target_day)

    print("Looking up today's market-moving headline...")
    news_item = get_news_item(args.newsapi_key, target_day)

    row = {
        "DATE": target_day.isoformat(),
        "DOW": values["DOW"] if values["DOW"] is not None else "",
        "S&P": values["S&P"] if values["S&P"] is not None else "",
        "US 10 YR (%)": values["US 10 YR"] if values["US 10 YR"] is not None else "",
        "WTI Crude Oil": values["WTI Crude Oil"] if values["WTI Crude Oil"] is not None else "",
        NEWS_COLUMN: news_item,
    }

    if args.dry_run:
        print(f"[dry-run] Not saving. Would have written: {row}")
        return

    rows = load_existing_rows(csv_path)
    rows[row["DATE"]] = row
    save_rows(csv_path, rows)

    print(f"Done — wrote {target_day.isoformat()} to {csv_path.name}:")
    for k, v in row.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()