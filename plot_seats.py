#!/usr/bin/env python3
"""Visualize seat projection results from seat_projection.json."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

INPUT_JSON = Path("seat_projection.json")
OUTPUT_PNG = Path("seat_projection.png")
MAJORITY = 172

PARTY_COLORS = {
    "LPC": "#D71920",
    "CPC": "#1A4782",
    "NDP": "#F4831F",
    "BQ":  "#00A0C6",
    "GPC": "#3D9B35",
    "PPC": "#4B0082",
}


def main() -> None:
    if not INPUT_JSON.exists():
        print(f"ERROR: {INPUT_JSON} not found. Run seat_projection.py first.")
        return

    with open(INPUT_JSON, encoding="utf-8") as f:
        data = json.load(f)

    parties_data = data["parties"]
    as_of = data.get("as_of", "")
    n_sims = data.get("simulations", 10_000)

    # Sort by mean seats descending
    sorted_parties = sorted(
        parties_data.items(), key=lambda x: x[1]["mean_seats"]
    )

    party_names = [p for p, _ in sorted_parties]
    means = [s["mean_seats"] for _, s in sorted_parties]
    low95 = [s["low95"] for _, s in sorted_parties]
    high95 = [s["high95"] for _, s in sorted_parties]
    colors = [PARTY_COLORS.get(p, "#888888") for p in party_names]

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
        MAJORITY, color="black", linestyle="--", linewidth=1.2,
        label=f"Majority ({MAJORITY} seats)", zorder=1,
    )
    ax.text(
        MAJORITY + 1, len(party_names) - 0.1,
        f"Majority\n({MAJORITY})",
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
        f"46th Canadian Federal Election — Seat Projection\n"
        f"as of {as_of}  ({n_sims:,} simulations, 95% CI shown)",
        fontsize=12,
    )
    ax.grid(axis="x", linestyle="--", alpha=0.4, zorder=0)
    ax.set_axisbelow(True)

    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150)
    print(f"Saved → {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
