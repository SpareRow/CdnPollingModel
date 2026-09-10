#!/usr/bin/env python3
"""Visualize seat projection results from seat_projection.json."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from jurisdictions import FEDERAL, Jurisdiction

MAJORITY = FEDERAL.majority
PARTY_COLORS = FEDERAL.party_colors


def main(cfg: Jurisdiction = FEDERAL) -> None:
    input_json = cfg.p("seat_projection.json")
    output_png = cfg.p("seat_projection.png")
    if not input_json.exists():
        print(f"ERROR: {input_json} not found. Run seat_projection.py first.")
        return

    with open(input_json, encoding="utf-8") as f:
        data = json.load(f)

    # Parties that can't win seats (an "Others" bucket) would render as an
    # empty bar, so leave them out of the chart.
    parties_data = {
        p: s for p, s in data["parties"].items() if p in cfg.seat_eligible
    }
    as_of = data.get("as_of", "")
    n_sims = data.get("simulations", cfg.n_simulations)
    majority = data.get("majority", cfg.majority)

    # Sort by mean seats descending
    sorted_parties = sorted(
        parties_data.items(), key=lambda x: x[1]["mean_seats"]
    )

    party_names = [p for p, _ in sorted_parties]
    means = [s["mean_seats"] for _, s in sorted_parties]
    low95 = [s["low95"] for _, s in sorted_parties]
    high95 = [s["high95"] for _, s in sorted_parties]
    colors = [cfg.party_colors.get(p, "#888888") for p in party_names]

    # Error bar sizes (distance from mean to CI bound)
    xerr_low = [m - lo for m, lo in zip(means, low95)]
    xerr_high = [hi - m for m, hi in zip(means, high95)]

    fig, ax = plt.subplots(figsize=(10, 5))

    y_pos = range(len(party_names))
    bars = ax.barh(
        y_pos, means,
        color=colors,
        height=0.6,
        zorder=2,
    )
    ax.errorbar(
        means, list(y_pos),
        xerr=[xerr_low, xerr_high],
        fmt="none",
        color="black",
        capsize=5,
        linewidth=1.5,
        zorder=3,
    )

    # Majority line
    ax.axvline(
        majority, color="black", linestyle="--", linewidth=1.2,
        label=f"Majority ({majority} seats)", zorder=1,
    )
    ax.text(
        majority + 1, len(party_names) - 0.1,
        f"Majority\n({majority})",
        fontsize=8, va="top",
    )

    # Labels on bars
    for i, (mean, lo, hi) in enumerate(zip(means, low95, high95)):
        ax.text(
            mean + max(xerr_high[i], 2) + 3,
            i,
            f"{mean:.0f}  [{lo}–{hi}]",
            va="center", fontsize=9,
        )

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(party_names, fontsize=11)
    ax.set_xlabel("Projected seats")
    ax.set_xlim(0, max(high95) + 60)
    ax.set_title(
        f"{cfg.title} — Seat Projection\n"
        f"as of {as_of}  ({n_sims:,} simulations, 95% CI shown)",
        fontsize=12,
    )
    ax.grid(axis="x", linestyle="--", alpha=0.4, zorder=0)
    ax.set_axisbelow(True)

    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    print(f"Saved → {output_png}")


if __name__ == "__main__":
    import argparse
    import jurisdictions

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", default="federal", choices=list(jurisdictions.ALL))
    main(jurisdictions.get(ap.parse_args().jurisdiction))
