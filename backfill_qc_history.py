#!/usr/bin/env python3
"""
Backfill qc/seat_history.csv from the archived polls.

    python3 backfill_qc_history.py [--weeks 13]

One-off. The Quebec seat history started empty, so the "seats over time" chart
had a single point. Both the provincial polls and the language crosstabs carry
their own dates, so the projection can simply be re-run as of each past date —
the weighting in polling_model already discounts polls by age relative to a
reference date, which is all that's needed to reconstruct what the model would
have said then.

Rows are produced by exactly the same code path as the live weekly run — same
simulation count, same seed, same group reconciliation — so backfilled and
future rows are directly comparable. Re-running is safe: existing dates are
left alone unless --overwrite is passed.
"""

import argparse
import csv
import sys
from datetime import date, timedelta

import jurisdictions
import polling_model
import seat_projection as sp
from qc_language_polls_scraper import compute_language_averages

QC = jurisdictions.get("qc")


def load_language_polls(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def project_as_of(reference: date, polls, lang_polls, ridings, poles, weights) -> dict | None:
    """Re-run the projection as it would have stood on `reference`."""
    national = polling_model.compute_average(polls, reference, QC)
    if not national:
        return None
    groups = compute_language_averages(lang_polls, reference, QC)
    if not groups:
        return None

    total = sum(max(r["total_votes"], 1) for r in ridings)
    previous = {
        p: sum(r["baseline"][p] * max(r["total_votes"], 1) for r in ridings) / total
        for p in QC.parties
    }

    seat_counts, _ = sp.run_simulations(
        ridings, groups, national, poles, previous, None, None, QC, weights,
    )

    row = {"date": reference.isoformat()}
    for p in QC.parties:
        counts = seat_counts[p]
        row[f"{p}_mean"] = round(sum(counts) / len(counts), 1)
        row[f"{p}_low"] = int(sp.percentile(counts, 2.5))
        row[f"{p}_high"] = int(sp.percentile(counts, 97.5))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weeks", type=int, default=13,
                    help="how many weekly points to reconstruct (default: 13)")
    ap.add_argument("--overwrite", action="store_true",
                    help="recompute dates already present")
    args = ap.parse_args()

    history_path = QC.p("seat_history.csv")
    ridings = sp.load_ridings(QC.p(QC.baseline_csv), QC)
    polls = polling_model.load_polls(QC.p("raw_polls.csv"), QC)
    lang_polls = load_language_polls(QC.p("regional_polls.csv"))

    baseline_path = QC.p(QC.group_baseline_json)
    poles = sp.load_group_baselines(baseline_path, QC)
    weights = sp.load_group_weights(baseline_path, QC)
    if poles is None or weights is None:
        sys.exit("ERROR: language baselines missing. Run build_qc_language_baseline.py.")

    existing: dict[str, dict] = {}
    if history_path.exists() and history_path.stat().st_size:
        with open(history_path, newline="", encoding="utf-8") as f:
            existing = {r["date"]: r for r in csv.DictReader(f)}

    # Weekly points ending today, matching the cadence of the live run.
    today = date.today()
    targets = [today - timedelta(weeks=w) for w in range(args.weeks - 1, -1, -1)]

    earliest_poll = min(p["date"] for p in polls)
    print(f"Backfilling {len(targets)} weekly points "
          f"({targets[0]} → {targets[-1]}); polls start {earliest_poll}")

    for reference in targets:
        key = reference.isoformat()
        if key in existing and not args.overwrite:
            print(f"  {key}  already present, skipping")
            continue
        if reference < earliest_poll:
            print(f"  {key}  before the first poll, skipping")
            continue

        row = project_as_of(reference, polls, lang_polls, ridings, poles, weights)
        if row is None:
            print(f"  {key}  no usable polling, skipping")
            continue
        existing[key] = row
        lead = "  ".join(
            f"{p} {row[f'{p}_mean']:.0f}" for p in QC.seat_eligible
        )
        print(f"  {key}  {lead}")

    fieldnames = ["date"]
    for p in QC.parties:
        fieldnames += [f"{p}_mean", f"{p}_low", f"{p}_high"]

    rows = [existing[k] for k in sorted(existing)]
    with open(history_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved → {history_path}  ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
