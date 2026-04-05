#!/usr/bin/env python3
"""
Scrape regional polling tables from Wikipedia's 46th Canadian federal election
opinion polling page and produce regional_average.json.

Applies the same weighting logic as polling_model.py (age decay × sample size ×
pollster rating) to produce a weighted average per region.

Regions scraped: Ontario, Quebec, British Columbia, Alberta, Manitoba/Saskatchewan.
Atlantic provinces and Territories have no dedicated regional table; the caller
falls back to national swing for those regions.
"""

import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from polling_model import (
    LAMBDA,
    DEFAULT_N,
    PARTIES,
    POLLSTER_RATINGS,
    age_weight,
    sample_weight,
    get_pollster_rating,
    weighted_stats,
)

URL = "https://en.wikipedia.org/wiki/Opinion_polling_for_the_46th_Canadian_federal_election"
OUTPUT_JSON = Path("regional_average.json")

# Map Wikipedia section headings → our region keys
REGION_HEADINGS: dict[str, str] = {
    "ontario": "ON",
    "quebec": "QC",
    "québec": "QC",
    "british columbia": "BC",
    "alberta": "AB",
    "alberta polls": "AB",
    "manitoba": "MB_SK",
    "saskatchewan": "MB_SK",
    "manitoba and saskatchewan": "MB_SK",
    "prairies": "MB_SK",
}

# Party abbreviation patterns that appear in Wikipedia table headers
PARTY_HEADER_MAP: dict[str, str] = {
    "lpc": "LPC",
    "lib": "LPC",
    "liberal": "LPC",
    "liberals": "LPC",
    "cpc": "CPC",
    "con": "CPC",
    "conservative": "CPC",
    "conservatives": "CPC",
    "ndp": "NDP",
    "new democratic": "NDP",
    "bq": "BQ",
    "bloc": "BQ",
    "bloc québécois": "BQ",
    "bloc quebecois": "BQ",
    "gpc": "GPC",
    "grn": "GPC",
    "green": "GPC",
    "ppc": "PPC",
    "people's party": "PPC",
}


# ── HTTP ──────────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; cdn-polling-model/1.0)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


# ── Date parsing ──────────────────────────────────────────────────────────────

def parse_wiki_date(raw: str) -> date | None:
    """Parse dates as written in Wikipedia polling tables (e.g. '17 Mar 2026')."""
    raw = raw.strip()
    # Strip footnote markers like [1]
    raw = re.sub(r"\[\d+\]", "", raw).strip()
    for fmt in (
        "%d %b %Y", "%d %B %Y",
        "%b %d, %Y", "%B %d, %Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


# ── Table header parsing ──────────────────────────────────────────────────────

def identify_party_columns(header_rows: list[list[str]]) -> dict[int, str]:
    """
    Given a list of header row cell texts, return {col_index: party_abbr}
    for columns identified as party vote-share columns.
    """
    party_cols: dict[int, str] = {}
    for row in header_rows:
        for i, cell in enumerate(row):
            key = cell.lower().strip()
            # Strip footnotes and whitespace
            key = re.sub(r"\[\d+\]", "", key).strip()
            if key in PARTY_HEADER_MAP:
                party_cols[i] = PARTY_HEADER_MAP[key]
    return party_cols


def get_cell_texts(row: Tag) -> list[str]:
    """Extract text from all th/td cells in a row, handling colspan by repeating."""
    cells = []
    for cell in row.find_all(["th", "td"]):
        text = cell.get_text(separator=" ", strip=True)
        # Repeat cell for colspan
        try:
            span = int(cell.get("colspan", 1))
        except (ValueError, TypeError):
            span = 1
        cells.extend([text] * span)
    return cells


# ── Table parsing ─────────────────────────────────────────────────────────────

def parse_polling_table(table: Tag, region: str) -> list[dict]:
    """
    Parse a Wikipedia polling table, returning a list of poll dicts with:
      {date, firm, region, LPC, CPC, NDP, BQ, GPC, PPC, sample_size}
    """
    rows = table.find_all("tr")
    if not rows:
        return []

    # Separate header rows (all th) from data rows (have td)
    header_rows = []
    data_rows = []
    for row in rows:
        tds = row.find_all("td")
        ths = row.find_all("th")
        if tds:
            data_rows.append(row)
        elif ths:
            header_rows.append(get_cell_texts(row))

    if not header_rows or not data_rows:
        return []

    party_cols = identify_party_columns(header_rows)
    if not party_cols:
        return []

    # Try to find date and firm columns from the last header row
    last_header = header_rows[-1] if header_rows else []
    date_col = -1
    firm_col = -1
    sample_col = -1
    for i, h in enumerate(last_header):
        key = h.lower().strip()
        if any(k in key for k in ("date", "conducted", "fieldwork")):
            date_col = i
        elif any(k in key for k in ("firm", "pollster", "company", "source")):
            firm_col = i
        elif any(k in key for k in ("sample", "n =", "n=", "size", "respondent")):
            sample_col = i

    # If we couldn't find firm/date by header, guess: col 0 = firm, col 1 = date
    if firm_col < 0:
        firm_col = 0
    if date_col < 0:
        date_col = 1

    polls = []
    for row in data_rows:
        cells = get_cell_texts(row)
        if len(cells) < max(party_cols.keys(), default=0) + 1:
            continue

        # Skip rows that are sub-headers or election result rows
        if cells and cells[0].lower() in ("election", "2025 election", "results", "2021 election"):
            continue

        # Date
        raw_date = cells[date_col] if date_col < len(cells) else ""
        poll_date = parse_wiki_date(raw_date)
        if poll_date is None:
            continue

        # Firm
        firm = cells[firm_col].strip() if firm_col < len(cells) else "Unknown"
        firm = re.sub(r"\[\d+\]", "", firm).strip()
        if not firm or firm.lower() in ("–", "-", ""):
            continue

        # Sample size
        n = DEFAULT_N
        if 0 <= sample_col < len(cells):
            try:
                n = int(re.sub(r"[^\d]", "", cells[sample_col]) or DEFAULT_N)
            except ValueError:
                n = DEFAULT_N
        if n <= 0:
            n = DEFAULT_N

        # Party percentages
        party_pcts: dict[str, float] = {}
        for col_idx, party in party_cols.items():
            if col_idx >= len(cells):
                continue
            raw = cells[col_idx].strip()
            raw = re.sub(r"[^\d.]", "", raw)
            try:
                val = float(raw)
                if 0 < val <= 100:
                    party_pcts[party] = val
            except ValueError:
                pass

        if not party_pcts:
            continue

        poll_row = {
            "date": poll_date,
            "firm": firm,
            "region": region,
            "n": n,
            "pcts": party_pcts,
        }
        polls.append(poll_row)

    return polls


# ── Section navigation ────────────────────────────────────────────────────────

def extract_regional_polls(soup: BeautifulSoup) -> dict[str, list[dict]]:
    """
    Walk the page's headings and tables to collect polls by region.
    Returns {region_key: [poll_dicts]}.
    """
    regional_polls: dict[str, list[dict]] = defaultdict(list)

    # Build an ordered list of (heading_level, heading_text, element) tuples
    # and tables, in document order.
    elements = []
    for el in soup.find_all(["h2", "h3", "h4", "table"]):
        if el.name in ("h2", "h3", "h4"):
            text = el.get_text(separator=" ", strip=True)
            text = re.sub(r"\[.*?\]", "", text).strip()
            elements.append(("heading", el.name, text, el))
        elif el.name == "table" and "wikitable" in el.get("class", []):
            elements.append(("table", None, None, el))

    current_region = None
    for kind, level, text, el in elements:
        if kind == "heading":
            # Check if this heading names a region
            key = text.lower().strip()
            if key in REGION_HEADINGS:
                current_region = REGION_HEADINGS[key]
            elif level == "h2":
                # Top-level non-region heading resets context
                # (but keep current_region for sub-tables within a "Regional polls" section)
                if key not in ("regional polls", "regional", "polls by region"):
                    current_region = None
        elif kind == "table" and current_region:
            polls = parse_polling_table(el, current_region)
            if polls:
                regional_polls[current_region].extend(polls)

    return dict(regional_polls)


# ── Weighted average ──────────────────────────────────────────────────────────

def compute_regional_average(
    polls: list[dict],
    reference: date,
) -> dict[str, dict]:
    """Apply polling_model weighting to a list of regional polls."""
    party_vw: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for poll in polls:
        if poll["date"] > reference:
            continue
        aw = age_weight(poll["date"], reference)
        sw = sample_weight(poll["n"])
        pr = get_pollster_rating(poll["firm"])
        w = aw * sw * pr

        for party, pct in poll["pcts"].items():
            party_vw[party].append((pct, w))

    result = {}
    for party in PARTIES:
        stats = weighted_stats(party_vw.get(party, []))
        if stats["mean"] is None:
            continue
        mean = stats["mean"]
        std = stats["std"] or 0.0
        result[party] = {
            "mean": round(mean, 2),
            "std": round(std, 2),
        }
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Fetching {URL} …")
    try:
        html = fetch_page(URL)
    except requests.RequestException as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")
    regional_polls = extract_regional_polls(soup)

    if not regional_polls:
        print("WARNING: no regional polls found. Check Wikipedia page structure.", file=sys.stderr)

    today = date.today()
    averages: dict[str, dict | None] = {
        "ON": None, "QC": None, "BC": None, "AB": None,
        "MB_SK": None, "Atlantic": None, "North": None,
    }

    total_polls = 0
    for region, polls in regional_polls.items():
        avg = compute_regional_average(polls, today)
        if avg:
            averages[region] = avg
            total_polls += len(polls)
            print(f"  {region:6s}: {len(polls)} polls → "
                  f"LPC {avg.get('LPC', {}).get('mean', '?')}%, "
                  f"CPC {avg.get('CPC', {}).get('mean', '?')}%")

    output = {
        "as_of": today.isoformat(),
        "regions": averages,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved → {OUTPUT_JSON}  ({total_polls} regional polls across {len(regional_polls)} regions)")


if __name__ == "__main__":
    main()
