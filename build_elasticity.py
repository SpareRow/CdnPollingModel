#!/usr/bin/env python3
"""
Build per-riding swing elasticity from 2019 and 2021 Elections Canada results.

Elasticity measures how much a riding historically amplifies or dampens the
province-wide swing. A riding with LPC elasticity 1.4 swings 40% harder than
the province average when LPC moves; one with 0.6 swings 40% softer.

Outputs: riding_elasticity.csv

Only needs to be re-run after a new federal election.
"""

import csv
import difflib
import math
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

# Import reusable pieces from elections_canada_scraper
from elections_canada_scraper import (
    fetch_csv,
    find_col,
    extract_party_from_candidate,
    aggregate_riding,
    normalize_province,
    map_region,
    PARTIES,
)

# Import baseline function from seat_projection
from seat_projection import compute_2025_regional_baselines

ELECTION_URLS = {
    2019: "https://elections.ca/res/rep/off/ovr2019app/51/data_donnees/table_tableau12.csv",
    2021: "https://elections.ca/res/rep/off/ovr2021app/53/data_donnees/table_tableau12.csv",
}
BASELINE_2025_CSV = Path("riding_results_2025.csv")
OUTPUT_CSV = Path("riding_elasticity.csv")

REGULARISATION_K = 1.5   # prior observations at elasticity=1.0
ELASTICITY_CAP   = (-1.0, 4.0)
MIN_PROV_SWING   = 1.0   # pp — skip if provincial swing is too small to be meaningful


# ── Normalise riding names for fuzzy matching ─────────────────────────────────

def normalise_name(s: str) -> str:
    """Lowercase, strip accents, collapse dashes/spaces."""
    # Decompose unicode and drop combining marks (strips accents)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = s.replace("--", " ").replace("-", " ").replace("/", " ")
    s = " ".join(s.split())
    return s


def build_name_index(ridings: list[dict], name_key: str = "riding_name") -> dict[str, dict]:
    """Build {normalised_name: riding_dict} for fast lookup."""
    return {normalise_name(r[name_key]): r for r in ridings}


def match_riding(name: str, index: dict[str, dict], threshold: float = 0.85) -> dict | None:
    """Try exact then fuzzy match. Returns matched riding dict or None."""
    norm = normalise_name(name)
    if norm in index:
        return index[norm]
    # Fuzzy
    candidates = list(index.keys())
    matches = difflib.get_close_matches(norm, candidates, n=1, cutoff=threshold)
    if matches:
        return index[matches[0]]
    return None


# ── Download and aggregate historical results ─────────────────────────────────

def fetch_election(year: int) -> list[dict]:
    """Download and aggregate one election's riding results."""
    url = ELECTION_URLS[year]
    print(f"  Fetching {year} results from Elections Canada …", end="", flush=True)
    rows = fetch_csv(url)

    # Detect district number column
    sample = rows[0]
    col_dist_num = find_col(sample, "Electoral District Number",
                            "No du district électoral", "ED Number")
    if not col_dist_num:
        print(f"\nERROR: can't find district number column in {year} data.")
        return []

    by_riding: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        key = r.get(col_dist_num, "").strip()
        if key:
            by_riding[key].append(r)

    ridings = []
    for key, riding_rows in sorted(by_riding.items()):
        rec = aggregate_riding(riding_rows)
        if rec:
            ridings.append(rec)

    print(f" {len(ridings)} ridings.")
    return ridings


# ── Provincial averages ───────────────────────────────────────────────────────

def provincial_averages(ridings: list[dict]) -> dict[str, dict[str, float]]:
    """
    Compute vote-weighted average per party per region.
    Returns {region: {party: avg_pct}}.
    Reuses same logic as compute_2025_regional_baselines() in seat_projection.py.
    """
    # Convert list-of-dicts to the format expected by seat_projection's function:
    # each riding needs "region", "baseline" (dict party→pct), "total_votes"
    riding_objs = []
    for r in ridings:
        riding_objs.append({
            "region": r["region"],
            "total_votes": r.get("total_votes", 1),
            "baseline": {p: r.get(f"{p}_pct", 0.0) for p in PARTIES},
        })
    return compute_2025_regional_baselines(riding_objs)


# ── Elasticity computation ────────────────────────────────────────────────────

def cap(v: float) -> float:
    lo, hi = ELASTICITY_CAP
    return max(lo, min(hi, v))


def regularise(observations: list[float], k: float = REGULARISATION_K) -> float:
    """Regularise mean of observations toward prior of 1.0."""
    n = len(observations)
    if n == 0:
        return 1.0
    raw_mean = sum(observations) / n
    return (n * raw_mean + k * 1.0) / (n + k)


def compute_elasticity(
    ridings_2025: list[dict],
    ridings_2021: list[dict],
    ridings_2019: list[dict],
) -> list[dict]:
    """
    For each 2025 riding, find matching 2021 and 2019 ridings, then compute
    per-party elasticity from the two election cycles.
    """
    prov_2019 = provincial_averages(ridings_2019)
    prov_2021 = provincial_averages(ridings_2021)
    prov_2025 = provincial_averages(ridings_2025)

    # Build name indexes for matching
    idx_2021 = build_name_index(ridings_2021)
    idx_2019 = build_name_index(ridings_2019)

    unmatched = []
    results = []

    for r25 in ridings_2025:
        code   = r25["riding_code"]
        name   = r25["riding_name"]
        region = r25["region"]

        r21 = match_riding(name, idx_2021)
        r19 = match_riding(name, idx_2019)

        party_elasticities: dict[str, list[float]] = {p: [] for p in PARTIES}

        # Cycle 2019→2021
        if r21 and r19:
            for p in PARTIES:
                prov_swing = prov_2021.get(region, {}).get(p, 0.0) - \
                             prov_2019.get(region, {}).get(p, 0.0)
                if abs(prov_swing) < MIN_PROV_SWING:
                    continue
                riding_swing = r21.get(f"{p}_pct", 0.0) - r19.get(f"{p}_pct", 0.0)
                e = cap(riding_swing / prov_swing)
                party_elasticities[p].append(e)

        # Cycle 2021→2025
        if r21:
            for p in PARTIES:
                prov_swing = prov_2025.get(region, {}).get(p, 0.0) - \
                             prov_2021.get(region, {}).get(p, 0.0)
                if abs(prov_swing) < MIN_PROV_SWING:
                    continue
                riding_swing = r25.get(f"{p}_pct", 0.0) - r21.get(f"{p}_pct", 0.0)
                e = cap(riding_swing / prov_swing)
                party_elasticities[p].append(e)

        n_obs = max(len(v) for v in party_elasticities.values()) if party_elasticities else 0
        if not r21:
            unmatched.append(name)

        row = {
            "riding_code": code,
            "riding_name": name,
            "province": r25["province"],
            "region": region,
            "n_obs": n_obs,
        }
        for p in PARTIES:
            row[f"{p}_e"] = round(regularise(party_elasticities[p]), 4)

        results.append(row)

    if unmatched:
        print(f"\n  Unmatched ridings (elasticity defaulted to 1.0): {len(unmatched)}")
        for n in sorted(unmatched):
            print(f"    {n}")

    matched = len(ridings_2025) - len(unmatched)
    print(f"\n  Matched {matched}/{len(ridings_2025)} ridings to historical data "
          f"({matched/len(ridings_2025)*100:.0f}%)")
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BASELINE_2025_CSV.exists():
        print(f"ERROR: {BASELINE_2025_CSV} not found. Run elections_canada_scraper.py first.")
        sys.exit(1)

    print("Loading 2025 riding baselines …")
    with open(BASELINE_2025_CSV, newline="", encoding="utf-8") as f:
        ridings_2025 = list(csv.DictReader(f))
    # Convert pct strings to floats
    for r in ridings_2025:
        for p in PARTIES:
            try:
                r[f"{p}_pct"] = float(r.get(f"{p}_pct", 0) or 0)
            except ValueError:
                r[f"{p}_pct"] = 0.0
        try:
            r["total_votes"] = int(r.get("total_votes", 1) or 1)
        except ValueError:
            r["total_votes"] = 1
    print(f"  {len(ridings_2025)} ridings.")

    print("\nDownloading historical Elections Canada results:")
    ridings_2021 = fetch_election(2021)
    ridings_2019 = fetch_election(2019)

    if not ridings_2021 or not ridings_2019:
        print("ERROR: failed to fetch historical data.", file=sys.stderr)
        sys.exit(1)

    print("\nComputing elasticity …")
    results = compute_elasticity(ridings_2025, ridings_2021, ridings_2019)

    # Summary stats
    for p in PARTIES:
        vals = [r[f"{p}_e"] for r in results]
        avg = sum(vals) / len(vals)
        below1 = sum(1 for v in vals if v < 0.95)
        above1 = sum(1 for v in vals if v > 1.05)
        print(f"  {p}: mean elasticity {avg:.2f}  "
              f"({below1} ridings <0.95, {above1} ridings >1.05)")

    # Save
    fieldnames = (
        ["riding_code", "riding_name", "province", "region"]
        + [f"{p}_e" for p in PARTIES]
        + ["n_obs"]
    )
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nSaved → {OUTPUT_CSV}  ({len(results)} ridings)")


if __name__ == "__main__":
    main()
