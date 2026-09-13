#!/usr/bin/env python3
"""
Build 2022 francophone / non-francophone vote baselines: qc/language_baseline_2022.json

One-off build script. Run after build_qc_baseline.py and
qc_language_polls_scraper.py; commit the JSON it writes.

    python3 build_qc_language_baseline.py

Why this is needed at all
-------------------------
Swinging by language needs a *starting point* for each language group, and
nobody publishes what francophones and non-francophones actually voted in 2022.
Two approaches were tried and rejected:

  · Ecological regression (share_d ~ a + b·f_d over the 127 divisions, then
    extrapolate to f=0 and f=1). The poles come out wildly wrong — the PLQ at
    85% of the non-francophone vote, the CAQ at −1% on a linear fit. The reason
    is the ecological fallacy: the lowest-francophone divisions are West Island,
    where francophones *also* vote heavily PLQ, so the fit charges all of that
    vote to non-francophones. It is also extrapolating past the data, since the
    least francophone division is still 21% francophone.

  · A published 2022 final-poll crosstab. None is available.

What this does instead
----------------------
Take the *shape* from the earliest crosstab we have (Léger, November 2022 — the
closest measurement to the October 2022 election), and the *level* from the
actual election result, which is known exactly. Iterative proportional fitting
reconciles the two against both constraints:

  1. each language pole sums to 100%
  2. recombining the poles by the francophone share of the electorate
     reproduces the real province-wide result, party by party

That correction is doing real work, not cosmetics: the November poll had the PQ
6pp above its actual result, and anchoring pulls it back to within ~1pp of the
published estimates of the 2022 francophone vote.

Residual limitation: the PCQ's non-francophone share still comes out around
15% against a literature estimate nearer 7%, inherited from the seed poll. Since
non-francophones are ~19% of the electorate that misplaces roughly 1.5pp of PCQ
vote between groups. The province-wide total stays exact by construction.
"""

import csv
import json
import sys
from pathlib import Path

from jurisdictions import QUEBEC

BASELINE_CSV = QUEBEC.p(QUEBEC.baseline_csv)
LANGUAGE_POLLS_CSV = QUEBEC.p("regional_polls.csv")
OUTPUT_JSON = QUEBEC.p("language_baseline_2022.json")

PARTIES = list(QUEBEC.parties)
GROUPS = ["FR", "NONFR"]
MAX_ITERATIONS = 500
TOLERANCE = 1e-6


def load_targets() -> tuple[dict[str, float], float]:
    """Province-wide 2022 result per party, and the francophone share of votes."""
    if not BASELINE_CSV.exists():
        sys.exit(f"ERROR: {BASELINE_CSV} not found. Run build_qc_baseline.py first.")

    rows = list(csv.DictReader(open(BASELINE_CSV, encoding="utf-8")))
    total = sum(float(r["total_votes"]) for r in rows)
    targets = {
        p: sum(float(r[f"{p}_pct"]) * float(r["total_votes"]) for r in rows) / total
        for p in PARTIES
    }
    # Vote-weighted francophone share — an approximation of the francophone
    # share of the electorate, which is what the recombination needs.
    franco_share = sum(
        float(r["franco_pct"]) / 100 * float(r["total_votes"]) for r in rows
    ) / total
    return targets, franco_share


def load_seed() -> dict[str, dict[str, float]]:
    """The earliest available crosstab, used as the shape to be anchored."""
    if not LANGUAGE_POLLS_CSV.exists():
        sys.exit(
            f"ERROR: {LANGUAGE_POLLS_CSV} not found. "
            "Run qc_language_polls_scraper.py first."
        )

    rows = [r for r in csv.DictReader(open(LANGUAGE_POLLS_CSV, encoding="utf-8"))
            if r["region"] in GROUPS and r["date"]]
    if not rows:
        sys.exit(f"ERROR: no language crosstab rows in {LANGUAGE_POLLS_CSV}.")

    earliest = min(r["date"] for r in rows)
    seed: dict[str, dict[str, float]] = {}
    for row in rows:
        if row["date"] != earliest:
            continue
        seed[row["region"]] = {
            p: float(row[p]) if row.get(p) not in ("", None) else 0.0
            for p in PARTIES
        }

    missing = [g for g in GROUPS if g not in seed]
    if missing:
        sys.exit(f"ERROR: seed poll {earliest} is missing group(s) {missing}.")
    print(f"  seed crosstab: {earliest}")
    return seed


def fit_poles(
    seed: dict[str, dict[str, float]],
    targets: dict[str, float],
    franco_share: float,
) -> dict[str, dict[str, float]]:
    """Iterative proportional fitting against both constraints."""
    poles = {g: dict(v) for g, v in seed.items()}

    for iteration in range(MAX_ITERATIONS):
        # 1. Scale each party across both groups so the recombination matches
        #    the actual province-wide result.
        for p in PARTIES:
            recombined = (franco_share * poles["FR"][p]
                          + (1 - franco_share) * poles["NONFR"][p])
            if recombined > 0:
                factor = targets[p] / recombined
                for g in GROUPS:
                    poles[g][p] *= factor

        # 2. Renormalise each group to 100%.
        for g in GROUPS:
            total = sum(poles[g].values())
            if total > 0:
                for p in PARTIES:
                    poles[g][p] *= 100.0 / total

        residual = max(
            abs(franco_share * poles["FR"][p]
                + (1 - franco_share) * poles["NONFR"][p] - targets[p])
            for p in PARTIES
        )
        if residual < TOLERANCE:
            print(f"  converged after {iteration + 1} iterations "
                  f"(residual {residual:.2e} pp)")
            break
    else:
        print(f"  WARNING: did not converge in {MAX_ITERATIONS} iterations "
              f"(residual {residual:.4f} pp)", file=sys.stderr)

    return poles


def check(poles: dict[str, dict[str, float]]) -> None:
    """
    Sanity gates. These encode well-established features of the Quebec
    electorate; failing one means the seed or the anchoring is wrong, not that
    the electorate changed.
    """
    failures = []
    if poles["NONFR"]["PLQ"] < 40:
        failures.append(f"PLQ non-francophone {poles['NONFR']['PLQ']:.1f}% should be >40%")
    if poles["NONFR"]["PQ"] > 8:
        failures.append(f"PQ non-francophone {poles['NONFR']['PQ']:.1f}% should be <8%")
    if not 43 <= poles["FR"]["CAQ"] <= 50:
        failures.append(f"CAQ francophone {poles['FR']['CAQ']:.1f}% should be 43–50%")
    for g in GROUPS:
        total = sum(poles[g].values())
        if abs(total - 100) > 0.01:
            failures.append(f"{g} sums to {total:.2f}%, not 100%")

    if failures:
        sys.exit("ERROR: implausible language baselines:\n  · " + "\n  · ".join(failures))
    print("  sanity checks passed")


def main() -> None:
    print("Building 2022 language baselines:")
    targets, franco_share = load_targets()
    print(f"  francophone share of votes: {franco_share:.1%}")
    seed = load_seed()
    poles = fit_poles(seed, targets, franco_share)
    check(poles)

    print(f"\n  {'party':6}{'FR':>8}{'NONFR':>8}{'province':>10}")
    for p in PARTIES:
        print(f"  {p:6}{poles['FR'][p]:8.1f}{poles['NONFR'][p]:8.1f}{targets[p]:10.1f}")

    output = {
        "source": "2022 election result anchored to the earliest Léger crosstab by IPF",
        "franco_share_of_votes": round(franco_share, 4),
        "province": {p: round(v, 2) for p, v in targets.items()},
        "regions": {
            g: {p: round(poles[g][p], 2) for p in PARTIES} for g in GROUPS
        },
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nSaved → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
