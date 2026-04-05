#!/usr/bin/env python3
"""
Canadian Federal Polling Model — Phase 1
Produces a weighted polling average with uncertainty bands.

Methodology (after 338Canada):
  weight = age_weight × sample_weight × pollster_rating
  age_weight  = exp(-λ × days_old),  λ = 0.05
  sample_weight = sqrt(n) / sqrt(1000)
  pollster_rating ≈ 0.9–1.2 based on historical accuracy
"""

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

LAMBDA = 0.05          # age-decay constant (~14-day half-life)
DEFAULT_N = 1000       # assumed sample size when missing
PARTIES = ["LPC", "CPC", "NDP", "BQ", "GPC", "PPC"]

POLLSTER_RATINGS: dict[str, float] = {
    "angus reid": 1.2,
    "angus reid institute": 1.2,
    "nanos": 1.2,
    "nanos research": 1.2,
    "leger": 1.1,
    "abacus": 1.1,
    "abacus data": 1.1,
    "mainstreet": 1.0,
    "mainstreet research": 1.0,
    "ekos": 1.0,
    "ekos research": 1.0,
    "innovative": 1.0,
    "innovative research": 1.0,
    "innovative research group": 1.0,
    "liaison": 0.9,
    "liaison strategies": 0.9,
    "research co": 0.9,
    "research co.": 0.9,
    "ipsos": 1.0,
    "forum": 0.9,
    "forum research": 0.9,
}

INPUT_CSV = Path("raw_polls.csv")
OUTPUT_JSON = Path("current_average.json")
OUTPUT_CSV = Path("polling_average.csv")


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_pollster_rating(firm: str) -> float:
    key = firm.lower().strip()
    # Exact match first
    if key in POLLSTER_RATINGS:
        return POLLSTER_RATINGS[key]
    # Partial match
    for k, v in POLLSTER_RATINGS.items():
        if k in key or key in k:
            return v
    return 1.0  # default for unknown pollsters


def age_weight(poll_date: date, reference: date) -> float:
    days_old = max(0, (reference - poll_date).days)
    return math.exp(-LAMBDA * days_old)


def sample_weight(n: int) -> float:
    return math.sqrt(n) / math.sqrt(DEFAULT_N)


def parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def load_polls(path: Path) -> list[dict]:
    if not path.exists():
        print(f"ERROR: {path} not found. Run canadianpolling_scraper.py first.", file=sys.stderr)
        sys.exit(1)

    polls = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = parse_date(row.get("date", ""))
            if d is None:
                continue
            try:
                n = int(row["sample_size"]) if row.get("sample_size") else DEFAULT_N
            except ValueError:
                n = DEFAULT_N

            party_pcts = {}
            for p in PARTIES:
                val = row.get(p, "").strip()
                if val:
                    try:
                        party_pcts[p] = float(val)
                    except ValueError:
                        pass

            if not party_pcts:
                continue

            polls.append({
                "date": d,
                "firm": row.get("firm", "Unknown"),
                "n": n,
                "pcts": party_pcts,
            })

    return polls


# ── Core model ────────────────────────────────────────────────────────────────

def weighted_stats(values_weights: list[tuple[float, float]]) -> dict:
    """Weighted mean and std for a list of (value, weight) pairs."""
    if not values_weights:
        return {"mean": None, "std": None}
    total_w = sum(w for _, w in values_weights)
    if total_w == 0:
        return {"mean": None, "std": None}
    mean = sum(v * w for v, w in values_weights) / total_w
    variance = sum(w * (v - mean) ** 2 for v, w in values_weights) / total_w
    return {"mean": mean, "std": math.sqrt(variance)}


def compute_average(polls: list[dict], reference: date) -> dict[str, dict]:
    """Compute weighted polling average for all parties as of reference date."""
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
            "low95": round(mean - 1.96 * std, 2),
            "high95": round(mean + 1.96 * std, 2),
        }
    return result


def compute_rolling(polls: list[dict], days: int = 90) -> list[dict]:
    """Compute daily weighted average for the last `days` days."""
    today = date.today()
    rows = []
    for offset in range(days - 1, -1, -1):
        ref = today - timedelta(days=offset)
        avg = compute_average(polls, ref)
        row = {"date": ref.isoformat()}
        for party, stats in avg.items():
            row[f"{party}_mean"] = stats["mean"]
            row[f"{party}_std"] = stats["std"]
        rows.append(row)
    return rows


# ── Output ────────────────────────────────────────────────────────────────────

def print_table(avg: dict[str, dict], reference: date) -> None:
    print(f"\n{'='*62}")
    print(f"  Canadian Federal Polling Average — {reference.isoformat()}")
    print(f"{'='*62}")
    print(f"  {'Party':<8} {'Mean':>7} {'Std':>6} {'95% CI':>18}")
    print(f"  {'-'*50}")
    # Sort by mean descending
    for party, stats in sorted(avg.items(), key=lambda x: -x[1]["mean"]):
        ci = f"[{stats['low95']:5.1f}%, {stats['high95']:5.1f}%]"
        print(f"  {party:<8} {stats['mean']:>6.1f}%  {stats['std']:>5.2f}  {ci:>20}")
    print(f"{'='*62}\n")


def save_json(avg: dict[str, dict], path: Path, reference: date) -> None:
    data = {"as_of": reference.isoformat(), "parties": avg}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved → {path}")


def save_rolling_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved → {path}  ({len(rows)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    polls = load_polls(INPUT_CSV)
    print(f"Loaded {len(polls)} polls from {INPUT_CSV}")

    if not polls:
        print("No valid polls found.", file=sys.stderr)
        sys.exit(1)

    today = date.today()
    current_avg = compute_average(polls, today)

    if not current_avg:
        print("Could not compute average — check poll data.", file=sys.stderr)
        sys.exit(1)

    print_table(current_avg, today)
    save_json(current_avg, OUTPUT_JSON, today)

    rolling = compute_rolling(polls, days=90)
    save_rolling_csv(rolling, OUTPUT_CSV)


if __name__ == "__main__":
    main()
