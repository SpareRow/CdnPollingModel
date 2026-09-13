#!/usr/bin/env python3
"""
Golden-file regression test for the federal model.

The model is deterministic — `random.Random(42)` in seat_projection.run_simulations
and a pure weighted average in polling_model — so a frozen set of inputs must
always reproduce the same outputs. This pins that, so the multi-jurisdiction
refactor can't silently change federal numbers.

The goldens live in tests/golden/<date>/ and are a *frozen copy*, deliberately
not the live files in the repo root: the weekly GitHub Action overwrites those
every Monday, and goldens pointing at them would rot within a week.

Usage:  python3 check_regression.py
Exit code 0 = all checks passed, 1 = a mismatch was found.
"""

import csv
import json
import sys
from datetime import date
from pathlib import Path

import polling_model
import seat_projection

GOLDEN = Path("tests/golden/2026-08-03")

failures: list[str] = []
checks_run = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks_run
    checks_run += 1
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        if detail:
            print(f"        {detail}")
        failures.append(label)


# ── Check 1: national polling average ─────────────────────────────────────────

def check_polling_average() -> None:
    print("\npolling_model.compute_average")
    with open(GOLDEN / "current_average.json", encoding="utf-8") as f:
        expected_doc = json.load(f)
    expected = expected_doc["parties"]
    reference = date.fromisoformat(expected_doc["as_of"])

    polls = polling_model.load_polls(GOLDEN / "raw_polls.csv")
    actual = polling_model.compute_average(polls, reference)

    check("party set", set(actual) == set(expected),
          f"got {sorted(actual)}, want {sorted(expected)}")
    for p in sorted(expected):
        for field in ("mean", "std", "low95", "high95"):
            a, e = actual.get(p, {}).get(field), expected[p][field]
            check(f"{p}.{field}", a == e, f"got {a!r}, want {e!r}")


# ── Check 2 & 3: seat projection ──────────────────────────────────────────────

def run_projection():
    """Replicate seat_projection.main()'s computation against the golden inputs."""
    ridings = seat_projection.load_ridings(GOLDEN / "riding_results_2025.csv")
    national_polling = seat_projection.load_national(GOLDEN / "current_average.json")
    regional_polling = seat_projection.load_regional(GOLDEN / "regional_average.json")
    regional_2025 = seat_projection.compute_2025_regional_baselines(ridings)

    total_w = sum(max(r["total_votes"], 1) for r in ridings)
    national_2025_pcts = {
        p: sum(r["baseline"][p] * max(r["total_votes"], 1) for r in ridings) / total_w
        for p in seat_projection.PARTIES
    }

    elasticity_map = seat_projection.load_elasticity(GOLDEN / "riding_elasticity.csv")
    vacancy_map = seat_projection.load_vacancies(GOLDEN / "riding_vacancy.csv")

    return ridings, seat_projection.run_simulations(
        ridings, regional_polling, national_polling,
        regional_2025, national_2025_pcts, elasticity_map, vacancy_map,
    )


def check_projection() -> None:
    ridings, (party_seat_counts, riding_share_sums) = run_projection()
    parties = seat_projection.PARTIES
    n_sims = seat_projection.N_SIMULATIONS

    # --- per-riding projected shares vs riding_projections.csv ---
    print("\nseat_projection.run_simulations → riding_projections.csv")
    with open(GOLDEN / "riding_projections.csv", newline="", encoding="utf-8") as f:
        expected_rows = {r["riding_code"]: r for r in csv.DictReader(f)}

    check("riding count", len(ridings) == len(expected_rows),
          f"got {len(ridings)}, want {len(expected_rows)}")

    mismatches: list[str] = []
    for riding in ridings:
        code = riding["code"]
        exp = expected_rows.get(code)
        if exp is None:
            mismatches.append(f"{code}: missing from golden")
            continue
        sums = riding_share_sums[code]
        mean_shares = {p: sums[p] / n_sims for p in parties}
        winner = max(parties, key=lambda p: mean_shares[p])
        if winner != exp["projected_winner"]:
            mismatches.append(
                f"{code} {riding['name']}: winner {winner} != {exp['projected_winner']}")
        for p in parties:
            got = round(mean_shares[p], 1)
            want = float(exp[f"Share_{p}"])
            if got != want:
                mismatches.append(
                    f"{code} {riding['name']}: Share_{p} {got} != {want}")

    check(f"all {len(ridings)} ridings reproduce exactly", not mismatches,
          "; ".join(mismatches[:5]) + (f" … +{len(mismatches) - 5} more"
                                       if len(mismatches) > 5 else ""))

    # --- seat totals vs seat_projection.json ---
    print("\nseat_projection.json (ignoring as_of)")
    with open(GOLDEN / "seat_projection.json", encoding="utf-8") as f:
        expected_doc = json.load(f)

    check("simulations", expected_doc["simulations"] == n_sims,
          f"golden {expected_doc['simulations']}, code {n_sims}")

    for p in parties:
        counts = party_seat_counts[p]
        actual = {
            "mean_seats": round(sum(counts) / len(counts), 1),
            "low95": int(seat_projection.percentile(counts, 2.5)),
            "high95": int(seat_projection.percentile(counts, 97.5)),
        }
        exp = expected_doc["parties"][p]
        check(f"{p} seats", actual == exp, f"got {actual}, want {exp}")


def main() -> int:
    if not GOLDEN.exists():
        print(f"ERROR: golden directory {GOLDEN} not found.", file=sys.stderr)
        return 1

    print(f"Golden regression — inputs from {GOLDEN}")
    check_polling_average()
    check_projection()

    print(f"\n{'=' * 56}")
    if failures:
        print(f"  FAILED — {len(failures)} of {checks_run} checks")
        for f in failures[:20]:
            print(f"    · {f}")
        print(f"{'=' * 56}")
        return 1
    print(f"  OK — all {checks_run} checks passed")
    print(f"{'=' * 56}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
