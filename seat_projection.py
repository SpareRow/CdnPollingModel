#!/usr/bin/env python3
"""
Riding-level seat projection for the 46th Canadian federal election.

Methodology:
  1. Load 2025 riding baselines (from elections_canada_scraper.py).
  2. Load regional polling averages (from wikipedia_scraper.py) and
     national averages (from polling_model.py → current_average.json).
  3. Compute regional swing vs the 2025 election result.
  4. Apply additive swing to each riding, then add an incumbency bonus.
  5. Run 10,000 Monte Carlo simulations sampling from polling uncertainty
     to produce seat-count distributions and per-riding win probabilities.
"""

import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

RIDING_CSV = Path("riding_results_2025.csv")
NATIONAL_JSON = Path("current_average.json")
REGIONAL_JSON = Path("regional_average.json")
OUTPUT_JSON = Path("seat_projection.json")
OUTPUT_CSV = Path("riding_projections.csv")

PARTIES = ["LPC", "CPC", "NDP", "BQ", "GPC", "PPC"]
N_SIMULATIONS = 10_000
INCUMBENCY_BONUS = 4.0  # percentage points added to 2025 winner's share


# ── Data loading ──────────────────────────────────────────────────────────────

def load_ridings(path: Path) -> list[dict]:
    if not path.exists():
        print(f"ERROR: {path} not found. Run elections_canada_scraper.py first.",
              file=sys.stderr)
        sys.exit(1)
    ridings = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            riding = {
                "code": row["riding_code"],
                "name": row["riding_name"],
                "province": row["province"],
                "region": row["region"],
                "winner": row["winner"],
                "baseline": {},
                "total_votes": int(row.get("total_votes", 0) or 0),
            }
            for p in PARTIES:
                try:
                    riding["baseline"][p] = float(row.get(f"{p}_pct", 0) or 0)
                except ValueError:
                    riding["baseline"][p] = 0.0
            ridings.append(riding)
    return ridings


def load_national(path: Path) -> dict[str, dict]:
    if not path.exists():
        print(f"ERROR: {path} not found. Run polling_model.py first.", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["parties"]  # {party: {mean, std, low95, high95}}


def load_regional(path: Path) -> dict[str, dict | None]:
    """Load regional_average.json, returning {} if file missing."""
    if not path.exists():
        print(f"WARNING: {path} not found — using national swing for all regions.",
              file=sys.stderr)
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("regions", {})


def load_elasticity(path: Path = Path("riding_elasticity.csv")) -> dict[str, dict[str, float]]:
    """
    Load per-riding swing elasticity from riding_elasticity.csv.
    Returns {riding_code: {party: elasticity}}.
    Falls back to {} (all elasticities default to 1.0) if file missing.
    """
    if not path.exists():
        return {}
    result = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["riding_code"]
            result[code] = {}
            for p in PARTIES:
                try:
                    result[code][p] = float(row.get(f"{p}_e", 1.0) or 1.0)
                except ValueError:
                    result[code][p] = 1.0
    return result


# ── Regional 2025 baseline ────────────────────────────────────────────────────

def compute_2025_regional_baselines(
    ridings: list[dict],
) -> dict[str, dict[str, float]]:
    """
    Compute 2025 average vote share per party per region,
    weighted by total_votes (larger ridings count more).
    """
    region_votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    region_total: dict[str, float] = defaultdict(float)

    for riding in ridings:
        region = riding["region"]
        w = max(riding["total_votes"], 1)
        for p in PARTIES:
            region_votes[region][p] += riding["baseline"][p] * w
        region_total[region] += w

    result: dict[str, dict[str, float]] = {}
    for region, party_wvotes in region_votes.items():
        total = region_total[region]
        result[region] = {p: party_wvotes[p] / total for p in PARTIES}
    return result


# ── Swing computation ─────────────────────────────────────────────────────────

def compute_swings(
    regional_polling: dict[str, dict | None],
    national_polling: dict[str, dict],
    regional_2025: dict[str, dict[str, float]],
    national_2025_pcts: dict[str, float],
) -> dict[str, dict[str, float]]:
    """
    For each region, compute additive swing:
      swing[region][party] = current_regional_mean - 2025_regional_avg

    Falls back to national swing for regions without dedicated polling.
    """
    # National swing as fallback
    national_swing: dict[str, float] = {}
    for p in PARTIES:
        curr = national_polling.get(p, {}).get("mean", 0.0) or 0.0
        base = national_2025_pcts.get(p, 0.0)
        national_swing[p] = curr - base

    ALL_REGIONS = ["ON", "QC", "BC", "AB", "MB_SK", "Atlantic", "North"]
    swings: dict[str, dict[str, float]] = {}

    for region in ALL_REGIONS:
        reg_avg = regional_polling.get(region)
        reg_base = regional_2025.get(region, {})

        if reg_avg and reg_base:
            swings[region] = {}
            for p in PARTIES:
                curr = reg_avg.get(p, {}).get("mean") if isinstance(reg_avg.get(p), dict) else None
                if curr is None:
                    swings[region][p] = national_swing.get(p, 0.0)
                else:
                    swings[region][p] = curr - reg_base.get(p, 0.0)
        else:
            swings[region] = dict(national_swing)

    return swings


# ── Riding projection ─────────────────────────────────────────────────────────

def incumbency_bonus(baseline: dict[str, float], winner: str) -> float:
    """
    Scale the incumbency bonus proportionally to the winner's actual 2025 margin.

    A 1-vote win gets ~0pp bonus; a dominant 20pp+ win gets the full INCUMBENCY_BONUS.
    Uses a sigmoid-like ramp: bonus = INCUMBENCY_BONUS × tanh(margin / 10).

    Examples at INCUMBENCY_BONUS = 4pp:
      margin  0pp → bonus  0.0pp
      margin  5pp → bonus  1.9pp
      margin 10pp → bonus  3.1pp
      margin 20pp → bonus  3.9pp
    """
    sorted_shares = sorted(baseline.values(), reverse=True)
    runner_up = sorted_shares[1] if len(sorted_shares) > 1 else 0.0
    margin = baseline.get(winner, 0.0) - runner_up
    margin = max(margin, 0.0)
    return INCUMBENCY_BONUS * math.tanh(margin / 10.0)


def project_riding(
    baseline: dict[str, float],
    swing: dict[str, float],
    incumbent_party: str,
    elasticity: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Apply swing (scaled by per-riding elasticity) to a riding baseline,
    add margin-scaled incumbency bonus, renormalise.
    Returns {party: projected_pct} summing to ~100.
    """
    e = elasticity or {}
    projected = {}
    for p in PARTIES:
        val = baseline.get(p, 0.0) + swing.get(p, 0.0) * e.get(p, 1.0)
        projected[p] = max(val, 0.0)

    # Margin-scaled incumbency bonus
    if incumbent_party in projected:
        projected[incumbent_party] += incumbency_bonus(baseline, incumbent_party)

    # Renormalise
    total = sum(projected.values())
    if total > 0:
        projected = {p: v / total * 100 for p, v in projected.items()}
    return projected


# ── Monte Carlo ───────────────────────────────────────────────────────────────

def sample_swing(
    regional_polling: dict[str, dict | None],
    national_polling: dict[str, dict],
    regional_2025: dict[str, dict[str, float]],
    national_2025_pcts: dict[str, float],
    rng: random.Random,
) -> dict[str, dict[str, float]]:
    """
    Draw one sample of regional polling from Normal(mean, std),
    then recompute swings with sampled values.
    """
    # Sample national polling
    sampled_national: dict[str, dict] = {}
    for p in PARTIES:
        stats = national_polling.get(p, {})
        mean = stats.get("mean", 0.0) or 0.0
        std = stats.get("std", 0.0) or 0.0
        sampled_national[p] = {"mean": rng.gauss(mean, std)}

    # Sample regional polling
    sampled_regional: dict[str, dict | None] = {}
    for region, reg_avg in regional_polling.items():
        if reg_avg is None:
            sampled_regional[region] = None
            continue
        sampled_regional[region] = {}
        for p in PARTIES:
            stats = reg_avg.get(p, {}) if isinstance(reg_avg.get(p), dict) else {}
            mean = stats.get("mean", 0.0) or 0.0
            std = stats.get("std", 0.0) or 0.0
            sampled_regional[region][p] = {"mean": rng.gauss(mean, std)}

    return compute_swings(
        sampled_regional, sampled_national, regional_2025, national_2025_pcts
    )


def run_simulations(
    ridings: list[dict],
    regional_polling: dict[str, dict | None],
    national_polling: dict[str, dict],
    regional_2025: dict[str, dict[str, float]],
    national_2025_pcts: dict[str, float],
    elasticity_map: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, list[int]], dict[str, dict[str, int]]]:
    """
    Run N_SIMULATIONS Monte Carlo draws.

    Returns:
      party_seat_counts: {party: [seat_count_per_sim]}  (length N_SIMULATIONS)
      riding_wins:       {riding_code: {party: win_count}}
    """
    emap = elasticity_map or {}
    rng = random.Random(42)
    party_seat_counts: dict[str, list[int]] = {p: [] for p in PARTIES}
    riding_wins: dict[str, dict[str, int]] = {
        r["code"]: defaultdict(int) for r in ridings
    }

    print(f"Running {N_SIMULATIONS:,} simulations…", end="", flush=True)
    for i in range(N_SIMULATIONS):
        if i % 1000 == 0:
            print(".", end="", flush=True)

        swings = sample_swing(
            regional_polling, national_polling,
            regional_2025, national_2025_pcts, rng
        )

        sim_seats = defaultdict(int)
        for riding in ridings:
            region = riding["region"]
            sw = swings.get(region, swings.get("Atlantic", {}))
            elasticity = emap.get(riding["code"])
            proj = project_riding(riding["baseline"], sw, riding["winner"], elasticity)
            winner = max(PARTIES, key=lambda p: proj.get(p, 0.0))
            sim_seats[winner] += 1
            riding_wins[riding["code"]][winner] += 1

        for p in PARTIES:
            party_seat_counts[p].append(sim_seats.get(p, 0))

    print(" done.")
    return party_seat_counts, riding_wins


# ── Statistics ────────────────────────────────────────────────────────────────

def percentile(data: list[float], pct: float) -> float:
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f, c = int(k), math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ridings = load_ridings(RIDING_CSV)
    print(f"Loaded {len(ridings)} ridings.")

    national_polling = load_national(NATIONAL_JSON)
    regional_polling = load_regional(REGIONAL_JSON)

    # 2025 baselines aggregated to region level
    regional_2025 = compute_2025_regional_baselines(ridings)

    # National 2025 average (weighted by riding size)
    total_w = sum(max(r["total_votes"], 1) for r in ridings)
    national_2025_pcts: dict[str, float] = {
        p: sum(r["baseline"][p] * max(r["total_votes"], 1) for r in ridings) / total_w
        for p in PARTIES
    }
    print("2025 national avg:", {p: round(v, 1) for p, v in national_2025_pcts.items()})

    # Per-riding elasticity (optional — falls back to 1.0 if file missing)
    elasticity_map = load_elasticity()
    if elasticity_map:
        print(f"Loaded elasticity for {len(elasticity_map)} ridings.")
    else:
        print("No riding_elasticity.csv found — using uniform swing (elasticity=1.0).")

    # Deterministic swing (for point estimate)
    det_swings = compute_swings(
        regional_polling, national_polling, regional_2025, national_2025_pcts
    )

    # Monte Carlo
    party_seat_counts, riding_wins = run_simulations(
        ridings, regional_polling, national_polling,
        regional_2025, national_2025_pcts, elasticity_map
    )

    # Aggregate seat statistics
    from datetime import date
    party_stats = {}
    for p in PARTIES:
        counts = party_seat_counts[p]
        mean_s = sum(counts) / len(counts)
        party_stats[p] = {
            "mean_seats": round(mean_s, 1),
            "low95": int(percentile(counts, 2.5)),
            "high95": int(percentile(counts, 97.5)),
        }

    # Per-riding point-estimate winner + win probabilities
    riding_output = []
    for riding in ridings:
        region = riding["region"]
        sw = det_swings.get(region, det_swings.get("Atlantic", {}))
        elasticity = elasticity_map.get(riding["code"])
        proj = project_riding(riding["baseline"], sw, riding["winner"], elasticity)
        det_winner = max(PARTIES, key=lambda p: proj.get(p, 0.0))

        wins = riding_wins[riding["code"]]
        total = sum(wins.values())
        probs = {p: round(wins.get(p, 0) / total, 3) if total else 0.0
                 for p in PARTIES}

        riding_output.append({
            "riding_code": riding["code"],
            "riding_name": riding["name"],
            "province": riding["province"],
            "projected_winner": det_winner,
            **{f"P_{p}": probs[p] for p in PARTIES},
        })

    # Print summary table
    print(f"\n{'='*56}")
    print(f"  Seat Projection — 46th Canadian Federal Election")
    print(f"{'='*56}")
    print(f"  {'Party':<8} {'Mean':>6}  {'95% CI':>16}")
    print(f"  {'-'*44}")
    for p, stats in sorted(party_stats.items(), key=lambda x: -x[1]["mean_seats"]):
        ci = f"[{stats['low95']:3d}, {stats['high95']:3d}]"
        print(f"  {p:<8} {stats['mean_seats']:>5.0f}   {ci:>16}")
    print(f"{'='*56}")
    total_mean = sum(s["mean_seats"] for s in party_stats.values())
    print(f"  Total mean seats: {total_mean:.0f}  (majority: 172)")
    print()

    # Save outputs
    output_data = {
        "as_of": date.today().isoformat(),
        "simulations": N_SIMULATIONS,
        "majority": 172,
        "parties": party_stats,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    print(f"Saved → {OUTPUT_JSON}")

    fieldnames = (
        ["riding_code", "riding_name", "province", "projected_winner"]
        + [f"P_{p}" for p in PARTIES]
    )
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(riding_output)
    print(f"Saved → {OUTPUT_CSV}  ({len(riding_output)} ridings)")


if __name__ == "__main__":
    main()
