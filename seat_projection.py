#!/usr/bin/env python3
"""
Riding-level seat projection.

Methodology:
  1. Load riding baselines from the previous election.
  2. Load swing-group polling averages and the overall average
     (from polling_model.py → current_average.json).
  3. Compute swing per group vs the previous election result.
  4. Apply that swing to each riding, then add an incumbency bonus
     (halved for ridings whose seat is currently vacant — see
     mp_vacancy_scraper.py — since there's no sitting MP to carry a
     personal incumbency advantage into the next election).
  5. Run Monte Carlo simulations sampling from polling uncertainty to produce
     seat-count distributions and per-riding expected vote shares.

Two things vary by jurisdiction (see jurisdictions.py):

  · How swing is applied. Federal uses additive swing (blend 0.0). That breaks
    when a party's support collapses or doubles — it drives the party negative
    in its weak ridings while understating the change in its strongholds — so
    Quebec uses multiplicative swing (blend 1.0), which is a uniform shift in
    log-odds space and handles both directions symmetrically.

  · What the swing groups are. Federal swings by region. Quebec has no
    published regional crosstabs, but does have francophone/non-francophone
    ones — which is also the axis that actually matters there — so each riding
    takes a weighted blend of the two according to its francophone share.
"""

import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

from jurisdictions import FEDERAL, Jurisdiction

PARTIES = list(FEDERAL.parties)
N_SIMULATIONS = FEDERAL.n_simulations
INCUMBENCY_BONUS = FEDERAL.incumbency_bonus_pp
OPEN_SEAT_DISCOUNT = FEDERAL.open_seat_discount

# Guard against log(0) when a party polls at or below zero in a swing group.
MULT_SWING_FLOOR = 0.2


# ── Data loading ──────────────────────────────────────────────────────────────

def load_ridings(path: Path, cfg: Jurisdiction = FEDERAL) -> list[dict]:
    if not path.exists():
        print(f"ERROR: {path} not found. Run the baseline builder first.",
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
            # Francophone share drives the language swing axis; absent for
            # jurisdictions that swing by region.
            if row.get("franco_pct"):
                try:
                    riding["franco_pct"] = float(row["franco_pct"])
                except ValueError:
                    pass
            for p in cfg.parties:
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


def load_group_baselines(
    path: Path, cfg: Jurisdiction
) -> dict[str, dict[str, float]] | None:
    """
    Previous-election shares per swing group, read from JSON.

    Used when the groups can't be derived by aggregating ridings. Returns None
    if the file is missing, so the caller falls back to aggregation.
    """
    if not path.exists():
        print(f"WARNING: {path} not found — aggregating group baselines from ridings.",
              file=sys.stderr)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    regions = data.get("regions", {})
    missing = [g for g in cfg.swing_groups if g not in regions]
    if missing:
        print(f"WARNING: {path} has no baseline for {missing} — falling back.",
              file=sys.stderr)
        return None
    return {g: {p: float(regions[g].get(p, 0.0)) for p in cfg.parties}
            for g in cfg.swing_groups}


def load_group_weights(path: Path, cfg: Jurisdiction) -> dict[str, float] | None:
    """
    Each swing group's share of the electorate, used to reconcile group polling
    against the overall average. Only meaningful for a language-style axis,
    where the groups partition voters rather than ridings.
    """
    if cfg.swing_axis != "language" or not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    franco = data.get("franco_share_of_votes")
    if franco is None:
        return None
    return {"FR": float(franco), "NONFR": 1.0 - float(franco)}


def load_regional(path: Path) -> dict[str, dict | None]:
    """Load regional_average.json, returning {} if file missing."""
    if not path.exists():
        print(f"WARNING: {path} not found — using national swing for all regions.",
              file=sys.stderr)
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("regions", {})


def load_elasticity(
    path: Path = Path("riding_elasticity.csv"), cfg: Jurisdiction = FEDERAL
) -> dict[str, dict[str, float]]:
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
            for p in cfg.parties:
                try:
                    result[code][p] = float(row.get(f"{p}_e", 1.0) or 1.0)
                except ValueError:
                    result[code][p] = 1.0
    return result


def load_vacancies(path: Path = Path("riding_vacancy.csv")) -> dict[str, bool]:
    """
    Load per-riding seat-vacancy flags from riding_vacancy.csv
    (see mp_vacancy_scraper.py). Returns {riding_code: seat_vacant}.
    Falls back to {} (no known vacancies) if file missing.
    """
    if not path.exists():
        return {}
    result = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["riding_code"]] = row.get("seat_vacant", "").strip().lower() == "true"
    return result


# ── Regional 2025 baseline ────────────────────────────────────────────────────

def compute_2025_regional_baselines(
    ridings: list[dict], cfg: Jurisdiction = FEDERAL
) -> dict[str, dict[str, float]]:
    """
    Compute previous-election average vote share per party per swing group,
    weighted by total_votes (larger ridings count more).

    For a language axis each riding contributes to both groups in proportion
    to its francophone share, rather than to a single group.
    """
    region_votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    region_total: dict[str, float] = defaultdict(float)

    for riding in ridings:
        w = max(riding["total_votes"], 1)
        for group, share in swing_weights(riding, cfg).items():
            if share <= 0:
                continue
            gw = w * share
            for p in cfg.parties:
                region_votes[group][p] += riding["baseline"][p] * gw
            region_total[group] += gw

    result: dict[str, dict[str, float]] = {}
    for region, party_wvotes in region_votes.items():
        total = region_total[region]
        result[region] = {p: party_wvotes[p] / total for p in cfg.parties}
    return result


# ── Swing groups ──────────────────────────────────────────────────────────────

def swing_weights(riding: dict, cfg: Jurisdiction = FEDERAL) -> dict[str, float]:
    """
    How much each swing group applies to this riding.

    "region" gives a one-hot vector — the riding's own region, weight 1.0 —
    which reproduces the original per-region lookup exactly. "language" splits
    the riding between the francophone and non-francophone groups.
    """
    if cfg.swing_axis == "province":
        return {cfg.swing_groups[0]: 1.0}
    if cfg.swing_axis == "language":
        f = riding.get("franco_pct")
        if f is None:
            return {g: 1.0 / len(cfg.swing_groups) for g in cfg.swing_groups}
        f = min(max(f / 100.0, 0.0), 1.0)
        return {"FR": f, "NONFR": 1.0 - f}
    return {riding["region"]: 1.0}


def blend_by_weights(
    per_group: dict[str, dict[str, float]],
    weights: dict[str, float],
    cfg: Jurisdiction = FEDERAL,
    fallback: dict[str, float] | None = None,
) -> dict[str, float]:
    """Combine per-group party values into one per-party dict for a riding."""
    out: dict[str, float] = {}
    for p in cfg.parties:
        total = 0.0
        for group, w in weights.items():
            source = per_group.get(group)
            if source is None:
                source = fallback or {}
            total += source.get(p, 0.0) * w
        out[p] = total
    return out


# ── Swing computation ─────────────────────────────────────────────────────────

def reconcile_groups(
    regional_polling: dict[str, dict | None],
    national_polling: dict[str, dict],
    group_weights: dict[str, float],
    cfg: Jurisdiction,
) -> dict[str, dict | None]:
    """
    Rescale group polling so it agrees with the overall polling average.

    Group crosstabs and the headline average come from different samples of
    pollsters, so they disagree on the level. Quebec's language crosstabs are
    Léger-only and recombine to PQ 31.7% while the full poll average says 29.2%
    — left alone, that imports one house's lean into every seat and makes the
    dashboard's own headline number inconsistent with its seat projection.

    So take the *shape* from the crosstabs and the *level* from the average:
    scale each party across the groups until the electorate-weighted
    recombination matches the overall average, renormalising each group to 100%
    in between. Same idea as anchoring the 2022 language baselines to the
    actual election result.
    """
    groups = [g for g in group_weights if regional_polling.get(g)]
    if not groups:
        return regional_polling

    means = {
        g: {p: (regional_polling[g].get(p) or {}).get("mean", 0.0) or 0.0
            for p in cfg.parties}
        for g in groups
    }
    weight_total = sum(group_weights[g] for g in groups)
    weights = {g: group_weights[g] / weight_total for g in groups}

    # Normalise the target to 100%. Each party's average is computed
    # independently across polls that don't all report the same parties, so the
    # headline numbers sum to slightly over 100 — and the two constraints below
    # (match the target, and each group sums to 100) are only compatible if the
    # target sums to 100 too. Without this the iteration oscillates instead of
    # converging, leaving a ~0.1pp residual.
    targets = {p: national_polling.get(p, {}).get("mean", 0.0) or 0.0
               for p in cfg.parties}
    target_total = sum(targets.values())
    if target_total > 0:
        targets = {p: v * 100.0 / target_total for p, v in targets.items()}

    for _ in range(100):
        worst = 0.0
        for p in cfg.parties:
            target = targets[p]
            combined = sum(weights[g] * means[g][p] for g in groups)
            if combined > 0 and target > 0:
                factor = target / combined
                for g in groups:
                    means[g][p] *= factor
                worst = max(worst, abs(combined - target))
        for g in groups:
            total = sum(means[g].values())
            if total > 0:
                for p in cfg.parties:
                    means[g][p] *= 100.0 / total
        if worst < 1e-9:
            break

    reconciled = dict(regional_polling)
    for g in groups:
        reconciled[g] = {
            p: {**(regional_polling[g].get(p) or {}), "mean": means[g][p]}
            for p in cfg.parties
        }
    return reconciled


def compute_swings(
    regional_polling: dict[str, dict | None],
    national_polling: dict[str, dict],
    regional_2025: dict[str, dict[str, float]],
    national_2025_pcts: dict[str, float],
    cfg: Jurisdiction = FEDERAL,
    group_weights: dict[str, float] | None = None,
) -> dict[str, dict[str, float]]:
    """
    For each region, compute additive swing:
      swing[region][party] = current_regional_mean - 2025_regional_avg

    Falls back to national swing for regions without dedicated polling.
    """
    if group_weights:
        regional_polling = reconcile_groups(
            regional_polling, national_polling, group_weights, cfg
        )

    # National swing as fallback
    national_swing: dict[str, float] = {}
    for p in cfg.parties:
        curr = national_polling.get(p, {}).get("mean", 0.0) or 0.0
        base = national_2025_pcts.get(p, 0.0)
        national_swing[p] = curr - base

    swings: dict[str, dict[str, float]] = {}

    for region in cfg.swing_groups:
        reg_avg = regional_polling.get(region)
        reg_base = regional_2025.get(region, {})

        if reg_avg and reg_base:
            swings[region] = {}
            for p in cfg.parties:
                curr = reg_avg.get(p, {}).get("mean") if isinstance(reg_avg.get(p), dict) else None
                if curr is None:
                    swings[region][p] = national_swing.get(p, 0.0)
                else:
                    swings[region][p] = curr - reg_base.get(p, 0.0)
        else:
            swings[region] = dict(national_swing)

    return swings


# ── Riding projection ─────────────────────────────────────────────────────────

def incumbency_bonus(
    baseline: dict[str, float],
    winner: str,
    is_vacant: bool = False,
    cfg: Jurisdiction = FEDERAL,
) -> float:
    """
    Scale the incumbency bonus proportionally to the winner's actual 2025 margin.

    A 1-vote win gets ~0pp bonus; a dominant 20pp+ win gets the full INCUMBENCY_BONUS.
    Uses a sigmoid-like ramp: bonus = INCUMBENCY_BONUS × tanh(margin / 10).

    Examples at INCUMBENCY_BONUS = 4pp:
      margin  0pp → bonus  0.0pp
      margin  5pp → bonus  1.9pp
      margin 10pp → bonus  3.1pp
      margin 20pp → bonus  3.9pp

    If the seat is currently vacant (no sitting MP — see mp_vacancy_scraper.py),
    the bonus is scaled down by OPEN_SEAT_DISCOUNT: part of the incumbency
    advantage is the departed MP's personal vote, which doesn't carry over,
    but some residual party/organizational strength in the riding still does.
    """
    if cfg.incumbency_bonus_pp == 0.0:
        return 0.0
    sorted_shares = sorted(baseline.values(), reverse=True)
    runner_up = sorted_shares[1] if len(sorted_shares) > 1 else 0.0
    margin = baseline.get(winner, 0.0) - runner_up
    margin = max(margin, 0.0)
    bonus = cfg.incumbency_bonus_pp * math.tanh(margin / 10.0)
    return bonus * cfg.open_seat_discount if is_vacant else bonus


def project_riding(
    baseline: dict[str, float],
    swing: dict[str, float],
    incumbent_party: str,
    elasticity: dict[str, float] | None = None,
    is_vacant: bool = False,
    cfg: Jurisdiction = FEDERAL,
    group_baseline: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Apply swing (scaled by per-riding elasticity) to a riding baseline,
    add margin-scaled incumbency bonus, renormalise.
    Returns {party: projected_pct} summing to ~100.

    With cfg.swing_blend == 0.0 this is plain additive swing. Above 0.0 it
    blends in multiplicative swing, which needs group_baseline to recover the
    ratio current/previous from the additive delta.
    """
    e = elasticity or {}
    blend = cfg.swing_blend
    projected = {}

    if blend == 0.0:
        for p in cfg.parties:
            val = baseline.get(p, 0.0) + swing.get(p, 0.0) * e.get(p, 1.0)
            projected[p] = max(val, 0.0)
    else:
        base_g = group_baseline or {}
        for p in cfg.parties:
            add = baseline.get(p, 0.0) + swing.get(p, 0.0) * e.get(p, 1.0)
            prev = max(base_g.get(p, 0.0), MULT_SWING_FLOOR)
            curr = max(base_g.get(p, 0.0) + swing.get(p, 0.0), MULT_SWING_FLOOR)
            # Uniform shift in log-odds space, scaled by riding elasticity.
            mult = baseline.get(p, 0.0) * math.exp(math.log(curr / prev) * e.get(p, 1.0))
            projected[p] = max((1.0 - blend) * add + blend * mult, 0.0)

    # Margin-scaled incumbency bonus (discounted for a currently vacant seat)
    if incumbent_party in projected:
        projected[incumbent_party] += incumbency_bonus(
            baseline, incumbent_party, is_vacant, cfg
        )

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
    cfg: Jurisdiction = FEDERAL,
    group_weights: dict[str, float] | None = None,
) -> dict[str, dict[str, float]]:
    """
    Draw one sample of regional polling from Normal(mean, std),
    then recompute swings with sampled values.

    When cfg.systematic_error_sd > 0 a single shared shock per party is added
    across every group, representing polling error that misses in the same
    direction everywhere. Without it the independent per-group draws cancel
    out and the seat intervals come out too narrow.

    The `> 0` guard matters beyond skipping dead work: rng.gauss(0, 0.0) still
    consumes the random stream, which would shift every subsequent draw and
    silently change the federal projection.
    """
    if cfg.systematic_error_sd > 0:
        shock = {p: rng.gauss(0.0, cfg.systematic_error_sd) for p in cfg.parties}
    else:
        shock = None

    # Sample national polling
    sampled_national: dict[str, dict] = {}
    for p in cfg.parties:
        stats = national_polling.get(p, {})
        mean = stats.get("mean", 0.0) or 0.0
        std = stats.get("std", 0.0) or 0.0
        value = rng.gauss(mean, std)
        if shock is not None:
            value += shock[p]
        sampled_national[p] = {"mean": value}

    # Sample regional polling
    sampled_regional: dict[str, dict | None] = {}
    for region, reg_avg in regional_polling.items():
        if reg_avg is None:
            sampled_regional[region] = None
            continue
        sampled_regional[region] = {}
        for p in cfg.parties:
            stats = reg_avg.get(p, {}) if isinstance(reg_avg.get(p), dict) else {}
            mean = stats.get("mean", 0.0) or 0.0
            std = stats.get("std", 0.0) or 0.0
            value = rng.gauss(mean, std)
            if shock is not None:
                value += shock[p]
            sampled_regional[region][p] = {"mean": value}

    return compute_swings(
        sampled_regional, sampled_national, regional_2025, national_2025_pcts, cfg,
        group_weights,
    )


def run_simulations(
    ridings: list[dict],
    regional_polling: dict[str, dict | None],
    national_polling: dict[str, dict],
    regional_2025: dict[str, dict[str, float]],
    national_2025_pcts: dict[str, float],
    elasticity_map: dict[str, dict[str, float]] | None = None,
    vacancy_map: dict[str, bool] | None = None,
    cfg: Jurisdiction = FEDERAL,
    group_weights: dict[str, float] | None = None,
) -> tuple[dict[str, list[int]], dict[str, dict[str, float]]]:
    """
    Run cfg.n_simulations Monte Carlo draws.

    Returns:
      party_seat_counts: {party: [seat_count_per_sim]}
      riding_share_sums: {riding_code: {party: sum_of_projected_pct_across_sims}}
    """
    emap = elasticity_map or {}
    vmap = vacancy_map or {}
    n_sims = cfg.n_simulations
    rng = random.Random(cfg.rng_seed)
    party_seat_counts: dict[str, list[int]] = {p: [] for p in cfg.parties}
    riding_share_sums: dict[str, dict[str, float]] = {
        r["code"]: {p: 0.0 for p in cfg.parties} for r in ridings
    }

    # Per-riding swing-group weights and blended previous-election baseline are
    # fixed across draws, so compute them once rather than 10,000 times.
    weights = [swing_weights(r, cfg) for r in ridings]
    group_baselines = [
        blend_by_weights(regional_2025, w, cfg, national_2025_pcts) for w in weights
    ]

    print(f"Running {n_sims:,} simulations…", end="", flush=True)
    for i in range(n_sims):
        if i % 1000 == 0:
            print(".", end="", flush=True)

        swings = sample_swing(
            regional_polling, national_polling,
            regional_2025, national_2025_pcts, rng, cfg, group_weights
        )

        sim_seats = defaultdict(int)
        for riding, w, base_g in zip(ridings, weights, group_baselines):
            sw = blend_by_weights(swings, w, cfg, {})
            elasticity = emap.get(riding["code"])
            is_vacant = vmap.get(riding["code"], False)
            proj = project_riding(
                riding["baseline"], sw, riding["winner"], elasticity, is_vacant,
                cfg, base_g,
            )
            winner = max(cfg.seat_eligible, key=lambda p: proj.get(p, 0.0))
            sim_seats[winner] += 1
            sums = riding_share_sums[riding["code"]]
            for p in cfg.parties:
                sums[p] += proj.get(p, 0.0)

        for p in cfg.parties:
            party_seat_counts[p].append(sim_seats.get(p, 0))

    print(" done.")
    return party_seat_counts, riding_share_sums


# ── Statistics ────────────────────────────────────────────────────────────────

def percentile(data: list[float], pct: float) -> float:
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f, c = int(k), math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(cfg: Jurisdiction = FEDERAL) -> None:
    riding_csv = cfg.p(cfg.baseline_csv)
    output_json = cfg.p("seat_projection.json")
    output_csv = cfg.p("riding_projections.csv")

    ridings = load_ridings(riding_csv, cfg)
    print(f"Loaded {len(ridings)} ridings.")
    if len(ridings) != cfg.seats_total:
        print(
            f"WARNING: {riding_csv} has {len(ridings)} ridings but {cfg.key} "
            f"has {cfg.seats_total} seats.",
            file=sys.stderr,
        )

    national_polling = load_national(cfg.p("current_average.json"))
    regional_polling = load_regional(cfg.p("regional_average.json"))

    # Previous-election baselines per swing group. Normally aggregated from the
    # ridings; a jurisdiction whose groups cut across ridings (Quebec's language
    # split) supplies them as JSON instead.
    regional_2025 = None
    group_weights = None
    if cfg.group_baseline_json:
        baseline_path = cfg.p(cfg.group_baseline_json)
        regional_2025 = load_group_baselines(baseline_path, cfg)
        group_weights = load_group_weights(baseline_path, cfg)
    if regional_2025 is None:
        regional_2025 = compute_2025_regional_baselines(ridings, cfg)
    if group_weights:
        print("Swing groups reconciled to the overall average, weights:",
              {g: round(w, 3) for g, w in group_weights.items()})

    # Overall previous-election average (weighted by riding size)
    total_w = sum(max(r["total_votes"], 1) for r in ridings)
    national_2025_pcts: dict[str, float] = {
        p: sum(r["baseline"][p] * max(r["total_votes"], 1) for r in ridings) / total_w
        for p in cfg.parties
    }
    print("Previous election avg:", {p: round(v, 1) for p, v in national_2025_pcts.items()})

    # Per-riding elasticity (optional — falls back to 1.0 if file missing)
    elasticity_map = load_elasticity(cfg.p("riding_elasticity.csv"), cfg)
    if elasticity_map:
        print(f"Loaded elasticity for {len(elasticity_map)} ridings.")
    else:
        print("No riding_elasticity.csv found — using uniform swing (elasticity=1.0).")

    # Currently-vacant seats (optional — falls back to no known vacancies if file missing)
    vacancy_map = load_vacancies(cfg.p("riding_vacancy.csv"))
    n_vacant = sum(vacancy_map.values())
    if vacancy_map:
        print(f"Loaded vacancy status for {len(vacancy_map)} ridings ({n_vacant} vacant).")
    else:
        print("No riding_vacancy.csv found — assuming no vacant seats.")

    # Monte Carlo
    party_seat_counts, riding_share_sums = run_simulations(
        ridings, regional_polling, national_polling,
        regional_2025, national_2025_pcts, elasticity_map, vacancy_map, cfg,
        group_weights,
    )

    # Aggregate seat statistics
    from datetime import date
    party_stats = {}
    for p in cfg.parties:
        counts = party_seat_counts[p]
        mean_s = sum(counts) / len(counts)
        party_stats[p] = {
            "mean_seats": round(mean_s, 1),
            "low95": int(percentile(counts, 2.5)),
            "high95": int(percentile(counts, 97.5)),
        }

    # Per-riding expected vote share (mean projected share across simulations)
    riding_output = []
    for riding in ridings:
        sums = riding_share_sums[riding["code"]]
        mean_shares = {p: sums[p] / cfg.n_simulations for p in cfg.parties}
        winner = max(cfg.seat_eligible, key=lambda p: mean_shares[p])

        riding_output.append({
            "riding_code": riding["code"],
            "riding_name": riding["name"],
            "province": riding["province"],
            "projected_winner": winner,
            **{f"Share_{p}": round(mean_shares[p], 1) for p in cfg.parties},
        })

    # Print summary table
    print(f"\n{'='*56}")
    print(f"  Seat Projection — {cfg.title}")
    print(f"{'='*56}")
    print(f"  {'Party':<8} {'Mean':>6}  {'95% CI':>16}")
    print(f"  {'-'*44}")
    for p, stats in sorted(party_stats.items(), key=lambda x: -x[1]["mean_seats"]):
        ci = f"[{stats['low95']:3d}, {stats['high95']:3d}]"
        print(f"  {p:<8} {stats['mean_seats']:>5.0f}   {ci:>16}")
    print(f"{'='*56}")
    total_mean = sum(s["mean_seats"] for s in party_stats.values())
    print(f"  Total mean seats: {total_mean:.0f}  (majority: {cfg.majority})")
    print()

    # Save outputs
    output_data = {
        "as_of": date.today().isoformat(),
        "simulations": cfg.n_simulations,
        "majority": cfg.majority,
        "parties": party_stats,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    print(f"Saved → {output_json}")

    fieldnames = (
        ["riding_code", "riding_name", "province", "projected_winner"]
        + [f"Share_{p}" for p in cfg.parties]
    )
    # Preserve previous projection for change tracking
    prev_csv = output_csv.with_name("riding_projections_prev.csv")
    if output_csv.exists():
        import shutil
        shutil.copy2(output_csv, prev_csv)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(riding_output)
    print(f"Saved → {output_csv}  ({len(riding_output)} ridings)")


if __name__ == "__main__":
    import argparse
    import jurisdictions

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", default="federal", choices=list(jurisdictions.ALL))
    main(jurisdictions.get(ap.parse_args().jurisdiction))
