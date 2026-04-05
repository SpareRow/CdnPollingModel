#!/usr/bin/env python3
"""
Fetch 45th General Election (April 28, 2025) riding-level results from
Elections Canada open data and save to riding_results_2025.csv.

Source: table_tableau12.csv — one row per candidate per riding.
We aggregate to riding level, keeping the six tracked parties.
"""

import csv
import io
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = (
    "https://elections.ca/res/rep/off/ovrGE45/62/data_donnees/table_tableau12.csv"
)
OUTPUT_CSV = Path("riding_results_2025.csv")
PARTIES = ["LPC", "CPC", "NDP", "BQ", "GPC", "PPC"]

# Elections Canada uses full party names; map to our abbreviations.
PARTY_MAP: dict[str, str] = {
    "liberal": "LPC",
    "liberal party of canada": "LPC",
    "conservative": "CPC",
    "conservative party of canada": "CPC",
    "ndp-new democratic party": "NDP",
    "new democratic party": "NDP",
    "ndp": "NDP",
    "bloc québécois": "BQ",
    "bloc quebecois": "BQ",
    "green party of canada": "GPC",
    "green party": "GPC",
    "people's party - ppc": "PPC",
    "people's party of canada": "PPC",
    "people's party": "PPC",
    "ppc": "PPC",
}

PROVINCE_TO_REGION: dict[str, str] = {
    "newfoundland and labrador": "Atlantic",
    "nova scotia": "Atlantic",
    "new brunswick": "Atlantic",
    "prince edward island": "Atlantic",
    "québec": "QC",
    "quebec": "QC",
    "ontario": "ON",
    "manitoba": "MB_SK",
    "saskatchewan": "MB_SK",
    "alberta": "AB",
    "british columbia": "BC",
    "northwest territories": "North",
    "nunavut": "North",
    "yukon": "North",
}


def map_party(affiliation: str) -> str | None:
    key = affiliation.lower().strip()
    if key in PARTY_MAP:
        return PARTY_MAP[key]
    # Partial match for edge cases
    for k, v in PARTY_MAP.items():
        if k in key:
            return v
    return None


def map_region(province: str) -> str:
    return PROVINCE_TO_REGION.get(province.lower().strip(), "Other")


def fetch_csv(url: str) -> list[dict]:
    """Download and parse the Elections Canada results CSV."""
    print(f"Fetching {url} …")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; cdn-polling-model/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()

    # Elections Canada CSVs are sometimes Latin-1 encoded
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Could not decode Elections Canada CSV")

    return list(csv.DictReader(io.StringIO(text)))


def find_col(row: dict, *candidates: str) -> str | None:
    """
    Find a column by name, handling Elections Canada's bilingual headers like
    'Electoral District Number/Numéro de circonscription'.
    Tries exact match first, then checks if any candidate string appears
    at the start of the column key.
    """
    lower_keys = {k: k.lower() for k in row}
    for candidate in candidates:
        cand_lower = candidate.lower()
        # Exact match
        for key, kl in lower_keys.items():
            if kl == cand_lower:
                return key
        # Prefix match (bilingual: "English name/French name")
        for key, kl in lower_keys.items():
            if kl.startswith(cand_lower):
                return key
        # Substring match (fallback)
        for key, kl in lower_keys.items():
            if cand_lower in kl:
                return key
    return None


def extract_party_from_candidate(candidate_str: str) -> str | None:
    """
    Elections Canada embeds party in the candidate string:
      "First Last PartyEnglish/PartyFrench"
    We match known party patterns anywhere in the string.
    """
    s = candidate_str.lower()
    # Order matters: more specific patterns first
    checks = [
        ("ndp-new democratic", "NDP"),
        ("new democratic party", "NDP"),
        ("ndp", "NDP"),
        ("liberal", "LPC"),
        ("conservative", "CPC"),
        ("bloc québécois", "BQ"),
        ("bloc quebecois", "BQ"),
        ("green party of canada", "GPC"),
        ("green party", "GPC"),
        ("people's party", "PPC"),
        ("parti populaire", "PPC"),
        ("ppc", "PPC"),
    ]
    for keyword, party in checks:
        if keyword in s:
            return party
    return None


def normalize_province(raw: str) -> str:
    """Strip bilingual province names like 'Ontario/Ontario' → 'Ontario'."""
    return raw.split("/")[0].strip()


def aggregate_riding(rows: list[dict]) -> dict | None:
    """
    Aggregate candidate rows for one riding into a single riding record.
    Returns None if required columns are missing.
    """
    if not rows:
        return None

    # Detect column names from first row
    sample = rows[0]
    col_dist_num = find_col(sample, "Electoral District Number",
                            "No du district électoral", "ED Number")
    col_dist_name = find_col(sample, "Electoral District Name",
                             "Nom du district électoral", "ED Name")
    col_province = find_col(sample, "Province")
    col_candidate = find_col(sample, "Candidate")
    col_votes = find_col(sample, "Votes Obtained", "Votes obtenus",
                         "Votes", "Total Votes")

    if not all([col_dist_num, col_dist_name, col_candidate, col_votes]):
        return None

    # First row gives riding metadata
    riding_code = rows[0].get(col_dist_num, "").strip()
    riding_name = rows[0].get(col_dist_name, "").strip()
    province_raw = rows[0].get(col_province, "").strip() if col_province else ""
    province = normalize_province(province_raw)

    # Sum votes per tracked party
    party_votes: dict[str, int] = defaultdict(int)
    total_valid = 0

    for r in rows:
        candidate_str = r.get(col_candidate, "").strip()
        party = extract_party_from_candidate(candidate_str)
        try:
            v = int(r.get(col_votes, "0").replace(",", "").strip() or 0)
        except ValueError:
            v = 0
        total_valid += v
        if party:
            party_votes[party] += v

    if total_valid == 0:
        return None

    # Convert to percentages
    pcts = {p: round(party_votes[p] / total_valid * 100, 2) for p in PARTIES}

    # Winner = party with highest vote share among tracked parties
    tracked = {p: party_votes[p] for p in PARTIES if party_votes[p] > 0}
    winner = max(tracked, key=tracked.get) if tracked else ""

    return {
        "riding_code": riding_code,
        "riding_name": riding_name,
        "province": province,
        "region": map_region(province),
        **{f"{p}_pct": pcts[p] for p in PARTIES},
        "winner": winner,
        "total_votes": total_valid,
    }


def main() -> None:
    try:
        rows = fetch_csv(URL)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Downloaded {len(rows)} candidate rows.")

    if not rows:
        print("No data found.", file=sys.stderr)
        sys.exit(1)

    # Detect district number column
    sample = rows[0]
    col_dist_num = find_col(sample, "Electoral District Number",
                            "No du district électoral", "ED Number")
    if not col_dist_num:
        print(f"ERROR: could not find district number column. Columns: {list(sample.keys())}", file=sys.stderr)
        sys.exit(1)

    # Group rows by riding
    by_riding: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = r.get(col_dist_num, "").strip()
        if key:
            by_riding[key].append(r)

    print(f"Found {len(by_riding)} ridings.")

    ridings = []
    for key, riding_rows in sorted(by_riding.items()):
        rec = aggregate_riding(riding_rows)
        if rec:
            ridings.append(rec)

    if not ridings:
        print("ERROR: no ridings aggregated.", file=sys.stderr)
        sys.exit(1)

    # Write output
    fieldnames = [
        "riding_code", "riding_name", "province", "region",
        "LPC_pct", "CPC_pct", "NDP_pct", "BQ_pct", "GPC_pct", "PPC_pct",
        "winner", "total_votes",
    ]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ridings)

    print(f"Saved {len(ridings)} ridings → {OUTPUT_CSV}")

    # Summary
    winners = defaultdict(int)
    for r in ridings:
        winners[r["winner"]] += 1
    print("2025 seat counts:", dict(sorted(winners.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
