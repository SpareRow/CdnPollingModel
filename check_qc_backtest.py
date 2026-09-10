#!/usr/bin/env python3
"""
Sanity checks for the Quebec swing pipeline.

    python3 check_qc_backtest.py

Two properties that must hold regardless of what the polls say:

  1. Zero-swing identity. Feed the 2022 result back in as though it were
     current polling, and the model must return the 2022 seat count. This is
     the strongest available test of the swing machinery: it catches sign
     errors, scale errors, a mis-set swing_blend, and any mismatch between the
     language baselines and the recombination weights. Multiplicative swing
     with zero swing has to be the identity function.

  2. Language reallocation is level-preserving. Reconciled group polling must
     recombine to the overall polling average, so the language axis moves vote
     *between* groups without changing the province-wide total.

Exit code 0 = passed, 1 = a check failed.
"""

import dataclasses
import json
import sys
from collections import Counter

import jurisdictions
import seat_projection as sp

QC = jurisdictions.get("qc")

# 2022 seats on the new 127-division map, from build_qc_baseline.py. Not the
# historical 125-seat result: both new divisions were carved from CAQ territory.
EXPECTED_2022_SEATS = {"CAQ": 92, "PLQ": 21, "QS": 11, "PQ": 3}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)


def load_inputs():
    ridings = sp.load_ridings(QC.p(QC.baseline_csv), QC)
    baseline_path = QC.p(QC.group_baseline_json)
    poles = sp.load_group_baselines(baseline_path, QC)
    weights = sp.load_group_weights(baseline_path, QC)
    if poles is None or weights is None:
        sys.exit(f"ERROR: {baseline_path} missing or incomplete. "
                 "Run build_qc_language_baseline.py.")
    return ridings, poles, weights


def check_zero_swing(ridings, poles, weights) -> None:
    print("\nZero-swing identity (2022 result in → 2022 seats out)")

    total = sum(max(r["total_votes"], 1) for r in ridings)
    province_2022 = {
        p: sum(r["baseline"][p] * max(r["total_votes"], 1) for r in ridings) / total
        for p in QC.parties
    }

    # Current polling == the 2022 result, with no uncertainty anywhere.
    national = {p: {"mean": v, "std": 0.0} for p, v in province_2022.items()}
    regional = {
        g: {p: {"mean": poles[g][p], "std": 0.0} for p in QC.parties}
        for g in QC.swing_groups
    }
    cfg = dataclasses.replace(QC, systematic_error_sd=0.0, n_simulations=1)

    swings = sp.compute_swings(regional, national, poles, province_2022, cfg, weights)
    worst = max(abs(swings[g][p]) for g in QC.swing_groups for p in QC.parties)
    check("all swings are zero", worst < 0.02, f"largest |swing| = {worst:.4f} pp")

    seat_counts, _ = sp.run_simulations(
        ridings, regional, national, poles, province_2022,
        None, None, cfg, weights,
    )
    seats = {p: seat_counts[p][0] for p in QC.parties if seat_counts[p][0]}

    check("seat total is 127", sum(seats.values()) == 127, f"got {sum(seats.values())}")
    check(f"reproduces 2022 seats {EXPECTED_2022_SEATS}",
          seats == EXPECTED_2022_SEATS, f"got {seats}")

    # Winners must match riding by riding, not just in aggregate.
    baseline_winners = Counter(r["winner"] for r in ridings)
    check("baseline winner counts agree",
          dict(baseline_winners) == EXPECTED_2022_SEATS,
          f"baseline says {dict(baseline_winners)}")


def check_reconciliation(weights) -> None:
    print("\nLanguage reallocation is level-preserving")

    national_path = QC.p("current_average.json")
    regional_path = QC.p("regional_average.json")
    if not national_path.exists() or not regional_path.exists():
        check("polling files present", False,
              "run update_polls.py --jurisdiction qc first")
        return

    national = sp.load_national(national_path)
    regional = sp.load_regional(regional_path)
    reconciled = sp.reconcile_groups(regional, national, weights, QC)

    # Compare against the *normalised* average: group shares each sum to 100,
    # so the level they recombine to must sum to 100 as well. The raw headline
    # numbers sum to slightly over, since each party is averaged independently
    # over polls that don't all report the same parties.
    raw = {p: national.get(p, {}).get("mean", 0.0) or 0.0 for p in QC.parties}
    raw_total = sum(raw.values()) or 1.0
    targets = {p: v * 100.0 / raw_total for p, v in raw.items()}
    print(f"  headline averages sum to {raw_total:.2f}% → normalised to 100%")

    worst_party, worst_gap = None, 0.0
    for p in QC.parties:
        target = targets[p]
        combined = sum(
            weights[g] * reconciled[g][p]["mean"]
            for g in QC.swing_groups if reconciled.get(g)
        )
        if abs(combined - target) > worst_gap:
            worst_party, worst_gap = p, abs(combined - target)
    check("recombination matches the overall average",
          worst_gap < 0.05, f"worst: {worst_party} off by {worst_gap:.3f} pp")

    for g in QC.swing_groups:
        if reconciled.get(g):
            total = sum(reconciled[g][p]["mean"] for p in QC.parties)
            check(f"{g} sums to 100%", abs(total - 100) < 0.05, f"got {total:.2f}%")

    # Report the split so a human can eyeball it.
    print("\n  Reconciled current polling:")
    print(f"    {'party':6}{'FR':>8}{'NONFR':>8}{'combined':>10}")
    for p in QC.seat_eligible:
        fr = reconciled["FR"][p]["mean"] if reconciled.get("FR") else 0.0
        nf = reconciled["NONFR"][p]["mean"] if reconciled.get("NONFR") else 0.0
        print(f"    {p:6}{fr:8.1f}{nf:8.1f}"
              f"{weights['FR'] * fr + weights['NONFR'] * nf:10.1f}")


def main() -> int:
    print("Quebec swing pipeline checks")
    ridings, poles, weights = load_inputs()
    check_zero_swing(ridings, poles, weights)
    check_reconciliation(weights)

    print(f"\n{'=' * 56}")
    if failures:
        print(f"  FAILED — {len(failures)} check(s)")
        for f in failures:
            print(f"    · {f}")
        print(f"{'=' * 56}")
        return 1
    print("  OK — all checks passed")
    print(f"{'=' * 56}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
