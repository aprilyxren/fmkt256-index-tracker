#!/usr/bin/env python3
"""
fill_journal.py

Fills in today's row of Journal_template_2026.xlsx ("Closing levels" sheet)
by pulling Dow, S&P 500, US 10-Yr Treasury yield, and WTI Crude from Stooq
(a CSV-based market data source, chosen because MarketWatch's bot protection
returns 401 Forbidden to automated requests — Stooq's endpoints are built
for exactly this kind of programmatic download and don't have that wall),
and pulling a short market-moving headline from NewsAPI.

It finds the row whose DATE cell matches the target date and only writes
into columns B-F (DOW, S&P, US 10 YR (%), WTI Crude Oil, News). Column A's
date formulas are never touched, so the template's date sequence stays
intact.

Requirements:
    pip install requests openpyxl

Usage:
    python fill_journal.py --excel "Journal_template_2026.xlsx" --newsapi-key YOUR_KEY
    python fill_journal.py --excel "Journal_template_2026.xlsx" --newsapi-key YOUR_KEY --date 2026-09-08
    python fill_journal.py --excel "Journal_template_2026.xlsx" --newsapi-key YOUR_KEY --dry-run
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from openpyxl import load_workbook

SHEET_NAME = "Closing levels"

# Column layout in the "Closing levels" sheet (1-indexed, matches the template)
COL_DATE = 1   # A: DATE
COL_DOW = 2    # B: DOW
COL_SPX = 3    # C: S&P
COL_TNX = 4    # D: US 10 YR (%)
COL_WTI = 5    # E: WTI Crude Oil
COL_NEWS = 6   # F: news item

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


# ---------------------------------------------------------------------------
# Price data (Stooq)
# ---------------------------------------------------------------------------

def fetch_stooq_close(name: str, symbol: str, target_day: date):
    """
    Download the daily CSV history for `symbol` and return (date, close)
    for the LAST row in the file. Returns None if the data isn't available
    yet for target_day (e.g. Stooq hasn't published today's close), or if
    the request fails outright.
    """
    url = STOOQ_URL_TEMPLATE.format(symbol=symbol)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    text = resp.text.strip()
    if not text or "," not in text:
        raise RuntimeError(f"Unexpected empty/invalid response for '{name}' ({symbol}) at {url}")

    lines = text.splitlines()
    header = lines[0].split(",")
    last_row = lines[-1].split(",")
    row = dict(zip(header, last_row))

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
# News: one short sentence, in the style of the template's sample row
# ---------------------------------------------------------------------------

def get_news_item(api_key: str, day: date) -> str:
    """
    Pull the single most relevant market/economy headline published on
    `day` from NewsAPI and format it as one short news item, matching the
    style of the template's sample row (a plain factual sentence).
    """
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
# Excel: find today's row and fill it in, without touching column A formulas
# ---------------------------------------------------------------------------

def find_row_for_date(excel_path: Path, target_day: date) -> int:
    """
    Open the workbook with cached formula VALUES (data_only=True) just to
    locate which row's DATE cell equals target_day. Returns the row number.
    """
    wb = load_workbook(excel_path, data_only=True)
    ws = wb[SHEET_NAME]
    for row in range(2, ws.max_row + 1):
        cell_val = ws.cell(row=row, column=COL_DATE).value
        if isinstance(cell_val, datetime) and cell_val.date() == target_day:
            return row
    raise ValueError(
        f"No row found for {target_day.isoformat()} in '{SHEET_NAME}'. "
        "Market may be closed that day, or the date is outside the template's range."
    )


def fill_row(excel_path: Path, row: int, values: dict, news_item: str, dry_run: bool = False) -> None:
    """Write DOW/S&P/US10YR/WTI + news into `row`, editing the ORIGINAL
    (formula-preserving) workbook so column A's date formulas stay intact."""
    wb = load_workbook(excel_path)  # NOT data_only — keeps formulas everywhere else
    ws = wb[SHEET_NAME]

    existing = [ws.cell(row=row, column=c).value for c in (COL_DOW, COL_SPX, COL_TNX, COL_WTI)]
    if any(v is not None for v in existing):
        print(f"[warn] Row {row} already has some values ({existing}) — overwriting.", file=sys.stderr)

    if values["DOW"] is not None:
        ws.cell(row=row, column=COL_DOW).value = values["DOW"]
    if values["S&P"] is not None:
        ws.cell(row=row, column=COL_SPX).value = values["S&P"]
    if values["US 10 YR"] is not None:
        ws.cell(row=row, column=COL_TNX).value = values["US 10 YR"]
    if values["WTI Crude Oil"] is not None:
        ws.cell(row=row, column=COL_WTI).value = values["WTI Crude Oil"]
    ws.cell(row=row, column=COL_NEWS).value = news_item

    if dry_run:
        print(f"[dry-run] Would write to row {row}: {values}, news={news_item!r}")
        return

    wb.save(excel_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fill today's row in the market journal template.")
    parser.add_argument("--excel", required=True, help="Path to Journal_template_2026.xlsx")
    parser.add_argument("--newsapi-key", required=True, help="Your NewsAPI.org API key.")
    parser.add_argument("--date", help="Target date as YYYY-MM-DD (default: today).")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't save.")
    args = parser.parse_args()

    excel_path = Path(args.excel)
    target_day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    if target_day.weekday() >= 5:
        print(f"{target_day.isoformat()} is a weekend — markets are closed, nothing to do.")
        return

    print(f"Locating row for {target_day.isoformat()} in {excel_path.name}...")
    row = find_row_for_date(excel_path, target_day)

    print("Fetching prices from Stooq...")
    values = scrape_all_series(target_day)

    print("Looking up today's market-moving headline...")
    news_item = get_news_item(args.newsapi_key, target_day)

    print(f"Writing row {row}...")
    fill_row(excel_path, row, values, news_item, dry_run=args.dry_run)

    print("Done:")
    for k, v in values.items():
        print(f"  {k}: {v}")
    print(f"  News: {news_item}")


if __name__ == "__main__":
    main()