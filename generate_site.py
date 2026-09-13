#!/usr/bin/env python3
"""
Generate docs/index.html — a self-contained dashboard for the Canadian
federal polling model. Called at the end of update_projections.py.

Sections:
  1. National polling average (line chart, 90-day rolling, 6 parties + CI)
  2. Seat projection (current bar chart + history line chart)
  3. Regional polling (line chart per region, dropdown selector)
  4. Riding projections (interactive sortable/filterable table)
"""

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from jurisdictions import ALL as ALL_JURISDICTIONS, FEDERAL, Jurisdiction
from polling_model import (
    age_weight,
    get_pollster_rating,
    parse_date,
    sample_weight,
    weighted_stats,
)

PARTIES = list(FEDERAL.parties)
PARTY_COLORS = FEDERAL.party_colors
REGION_LABELS = FEDERAL.swing_group_labels
PROVINCE_ORDER = list(FEDERAL.group_order)
PROVINCE_SHORT = FEDERAL.group_short


# ── Data loading ──────────────────────────────────────────────────────────────

def load_national_rolling(cfg: Jurisdiction = FEDERAL) -> list[dict]:
    """Read polling_average.csv → list of {date, <PARTY>_mean, <PARTY>_std, ...}."""
    path = cfg.p("polling_average.csv")
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_current_averages(cfg: Jurisdiction = FEDERAL) -> dict:
    path = cfg.p("current_average.json")
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_seat_projection(cfg: Jurisdiction = FEDERAL) -> dict:
    path = cfg.p("seat_projection.json")
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_riding_projections(cfg: Jurisdiction = FEDERAL) -> list[dict]:
    path = cfg.p("riding_projections.csv")
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # The projections CSV carries province but not region, so for a jurisdiction
    # that groups the table by region we join it back from the baseline. Doing
    # it here rather than widening riding_projections.csv keeps the federal
    # output byte-identical.
    if rows and cfg.group_field not in rows[0]:
        baseline = cfg.p(cfg.baseline_csv)
        if baseline.exists():
            with open(baseline, newline="", encoding="utf-8") as f:
                lookup = {
                    r["riding_code"]: r.get(cfg.group_field, "")
                    for r in csv.DictReader(f)
                }
            for r in rows:
                r[cfg.group_field] = lookup.get(r["riding_code"], "")
    return rows


def load_prev_winners(cfg: Jurisdiction = FEDERAL) -> dict[str, str]:
    """Return {riding_code: projected_winner} from the previous run, or {}."""
    prev = cfg.p("riding_projections_prev.csv")
    if not prev.exists():
        return {}
    with open(prev, newline="", encoding="utf-8") as f:
        return {r["riding_code"]: r["projected_winner"] for r in csv.DictReader(f)}


# ── Seat history ──────────────────────────────────────────────────────────────

def update_seat_history(seat_data: dict, cfg: Jurisdiction = FEDERAL) -> None:
    """Append today's seat projection to seat_history.csv (if not already present)."""
    path = cfg.p("seat_history.csv")
    today = date.today().isoformat()
    parties_data = seat_data.get("parties", {})

    fieldnames = ["date"]
    for p in cfg.parties:
        fieldnames += [f"{p}_mean", f"{p}_low", f"{p}_high"]

    # Read existing rows
    existing = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    # Don't duplicate today
    if any(r["date"] == today for r in existing):
        return

    new_row = {"date": today}
    for p in cfg.parties:
        stats = parties_data.get(p, {})
        new_row[f"{p}_mean"] = stats.get("mean_seats", "")
        new_row[f"{p}_low"]  = stats.get("low95", "")
        new_row[f"{p}_high"] = stats.get("high95", "")

    existing.append(new_row)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)


def load_seat_history(cfg: Jurisdiction = FEDERAL) -> list[dict]:
    path = cfg.p("seat_history.csv")
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Regional rolling averages ─────────────────────────────────────────────────

def compute_regional_rolling(
    days: int = 90, cfg: Jurisdiction = FEDERAL
) -> dict[str, list[dict]]:
    """
    Compute 90-day rolling weighted average per swing group from regional_polls.csv.
    Returns {group: [{date, <PARTY>_mean, <PARTY>_std, ...}, ...]}.
    """
    path = cfg.p("regional_polls.csv")
    if not path.exists():
        return {}

    # Load and parse regional polls
    polls_by_region: dict[str, list[dict]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = parse_date(row.get("date", ""))
            if d is None:
                continue
            region = row.get("region", "")
            if not region:
                continue
            try:
                n = int(row.get("sample_size") or cfg.default_sample_n)
            except ValueError:
                n = cfg.default_sample_n
            pcts = {}
            for p in cfg.parties:
                val = row.get(p, "")
                if val:
                    try:
                        pcts[p] = float(val)
                    except ValueError:
                        pass
            if pcts:
                polls_by_region[region].append({
                    "date": d, "firm": row.get("firm", ""), "n": n, "pcts": pcts
                })

    today = date.today()
    result: dict[str, list[dict]] = {}

    for region, polls in polls_by_region.items():
        rows = []
        for offset in range(days - 1, -1, -1):
            ref = today - timedelta(days=offset)
            party_vw: dict[str, list] = defaultdict(list)
            for poll in polls:
                if poll["date"] > ref:
                    continue
                aw = age_weight(poll["date"], ref, cfg)
                sw = sample_weight(poll["n"], cfg)
                pr = get_pollster_rating(poll["firm"], cfg)
                w  = aw * sw * pr
                for p, pct in poll["pcts"].items():
                    party_vw[p].append((pct, w))

            row = {"date": ref.isoformat()}
            for p in cfg.parties:
                stats = weighted_stats(party_vw.get(p, []))
                if stats["mean"] is not None:
                    row[f"{p}_mean"] = round(stats["mean"], 2)
                    row[f"{p}_std"]  = round(stats["std"] or 0.0, 2)
                else:
                    row[f"{p}_mean"] = None
                    row[f"{p}_std"]  = None
            rows.append(row)
        result[region] = rows

    return result


# ── HTML generation ───────────────────────────────────────────────────────────

def _riding_table_html(
    ridings: list[dict],
    seat_data: dict,
    prev_winners: dict[str, str] | None = None,
    cfg: Jurisdiction = FEDERAL,
) -> str:
    """Return the riding projections table + controls HTML fragment."""
    parties_data = seat_data.get("parties", {})
    as_of = seat_data.get("as_of", "")
    n_sims = seat_data.get("simulations", cfg.n_simulations)
    group_label = "Prov" if cfg.group_field == "province" else "Region"

    # Summary cells — an "Others" bucket can't win seats, so don't show it
    summary_parties = [p for p in parties_data if p in cfg.seat_eligible]
    summary_cells = ""
    for p in sorted(summary_parties, key=lambda x: -parties_data[x]["mean_seats"]):
        s = parties_data[p]
        color = cfg.party_colors.get(p, "#888")
        summary_cells += (
            f'<div class="summary-party">'
            f'<div class="summary-badge" style="background:{color}">{p}</div>'
            f'<div class="summary-seats">{s["mean_seats"]:.0f}</div>'
            f'<div class="summary-ci">[{s["low95"]}–{s["high95"]}]</div>'
            f'</div>'
        )

    # Group ridings for display. Anything whose group isn't in cfg.group_order
    # is appended rather than dropped: silently discarding unknown groups would
    # render an empty table with no error (which is what happened to Quebec,
    # whose divisions group by region, not province).
    by_group: dict[str, list] = defaultdict(list)
    for r in ridings:
        by_group[r.get(cfg.group_field, "") or "Unknown"].append(r)

    ordered_groups = [g for g in cfg.group_order if by_group.get(g)]
    extra_groups = sorted(g for g in by_group if g not in cfg.group_order)
    if extra_groups:
        n_extra = sum(len(by_group[g]) for g in extra_groups)
        print(
            f"  WARNING: {n_extra} ridings in {len(extra_groups)} group(s) not in "
            f"{cfg.key} group_order: {extra_groups[:5]}",
            file=sys.stderr,
        )
    ordered_groups += extra_groups

    n_cols = 4 + len(cfg.parties)
    table_rows = ""
    for prov in ordered_groups:
        prov_ridings = sorted(by_group.get(prov, []), key=lambda r: r["riding_name"])
        if not prov_ridings:
            continue
        short = cfg.group_short.get(prov, prov[:3].upper())

        prov_seats: dict[str, int] = {}
        for r in prov_ridings:
            w = r["projected_winner"]
            prov_seats[w] = prov_seats.get(w, 0) + 1
        seat_summary = "  ".join(
            f'<span class="pseat" style="color:{cfg.party_colors.get(p,"#888")}">{p}&nbsp;{n}</span>'
            for p, n in sorted(prov_seats.items(), key=lambda x: -x[1])
        )
        table_rows += (
            f'<tr class="province-header" data-province="{prov}">'
            f'<td colspan="{n_cols}"><span class="prov-name">{prov}</span>'
            f'<span class="prov-seats">{seat_summary}</span></td></tr>\n'
        )

        for r in prov_ridings:
            winner = r["projected_winner"]
            shares = {p: float(r.get(f"Share_{p}", 0) or 0) for p in cfg.parties}
            contested = sorted(
                (v for p, v in shares.items() if p in cfg.seat_eligible), reverse=True
            )
            margin = contested[0] - (contested[1] if len(contested) > 1 else 0.0)
            w_color = cfg.party_colors.get(winner, "#888")

            if margin < 5:
                comp_cls, comp_lbl = "comp-tossup", "Toss-up"
            elif margin < 15:
                comp_cls, comp_lbl = "comp-likely", "Likely"
            else:
                comp_cls, comp_lbl = "comp-safe",   "Safe"

            # Change indicator
            prev_winner = (prev_winners or {}).get(r["riding_code"])
            changed = prev_winner and prev_winner != winner
            change_badge = ""
            if changed:
                prev_color = cfg.party_colors.get(prev_winner, "#aaa")
                change_badge = (
                    f' <span class="change-badge" title="Previously projected: {prev_winner}">'
                    f'<span class="change-from" style="background:{prev_color}">{prev_winner}</span>'
                    f'<span class="change-arrow">→</span>'
                    f'<span class="change-to" style="background:{w_color}">{winner}</span>'
                    f'</span>'
                )

            badge = (
                f'<span class="winner-badge" style="background:{w_color}">{winner}</span>'
            )
            party_cells = ""
            for p in cfg.parties:
                share = shares.get(p, 0)
                if share < 0.5:
                    party_cells += '<td class="prob-td"></td>\n'
                else:
                    color = cfg.party_colors.get(p, "#aaa")
                    party_cells += (
                        f'<td class="prob-td">'
                        f'<div class="prob-cell">'
                        f'<div class="bar-wrap"><div class="bar" style="width:{min(share,100):.1f}%;background:{color}"></div></div>'
                        f'<span class="prob-label">{share:.0f}%</span>'
                        f'</div></td>\n'
                    )

            table_rows += (
                f'<tr class="riding-row{"  changed" if changed else ""}" '
                f'data-riding="{r["riding_name"].lower()}" '
                f'data-province="{prov.lower()}" '
                f'data-winner="{winner}" '
                f'data-margin="{margin:.1f}">\n'
                f'  <td class="riding-name">{r["riding_name"]}{change_badge}</td>\n'
                f'  <td class="prov-code">{short}</td>\n'
                f'  <td class="winner-td">{badge}</td>\n'
                f'  <td class="comp-td"><span class="comp-label {comp_cls}">{comp_lbl}</span></td>\n'
                + party_cells +
                f'</tr>\n'
            )

    province_options = "".join(
        f'<option value="{g.lower()}">{g}</option>' for g in ordered_groups
    )
    party_headers = "".join(f"<th>{p}</th>" for p in cfg.parties)

    return f"""
<div class="riding-summary">
  {summary_cells}
  <div class="majority-note">Majority: {cfg.majority} seats<br><small>{n_sims:,} simulations · as of {as_of}</small></div>
</div>
<div class="controls">
  <input type="text" id="search" placeholder="Search riding…" oninput="filterTable()">
  <select id="prov-filter" onchange="filterTable()">
    <option value="">All {"provinces" if cfg.group_field == "province" else "regions"}</option>
    {province_options}
  </select>
  <select id="winner-filter" onchange="filterTable()">
    <option value="">All parties</option>
    {"".join(f'<option value="{p}">{p}</option>' for p in cfg.seat_eligible)}
  </select>
  <select id="comp-filter" onchange="filterTable()">
    <option value="">All races</option>
    <option value="tossup">Toss-ups (&lt;5pt margin)</option>
    <option value="likely">Likely (5–15pt margin)</option>
    <option value="safe">Safe (≥15pt margin)</option>
  </select>
  <select id="change-filter" onchange="filterTable()">
    <option value="">All ridings</option>
    <option value="changed">Changed this week</option>
  </select>
</div>
<div class="result-count" id="result-count"></div>
<div class="table-wrap">
  <table id="riding-table">
    <thead>
      <tr>
        <th onclick="sortTable(0)">Riding</th>
        <th onclick="sortTable(1)">{group_label}</th>
        <th onclick="sortTable(2)">Winner</th>
        <th onclick="sortTable(3)">Confidence</th>
        {party_headers}
      </tr>
    </thead>
    <tbody id="table-body">
      {table_rows}
    </tbody>
  </table>
</div>"""


def _regional_section_html(cfg: Jurisdiction, region_labels: dict[str, str]) -> str:
    """
    The regional polling section, or nothing.

    Only rendered when the jurisdiction actually has group-level crosstabs.
    Quebec has none published — its sub-provincial signal is the
    francophone/non-francophone split — so the section is omitted entirely
    rather than shown empty.
    """
    if not region_labels:
        return ""
    options = "".join(
        f'<option value="{k}">{v}</option>' for k, v in region_labels.items()
    )
    if cfg.swing_axis == "language":
        label, heading = "Group", "Polling by Language"
        note = ("Francophone and non-francophone crosstabs. These are rescaled so "
                "they recombine to the overall polling average, so the split "
                "reallocates vote between groups without shifting the province-wide "
                "total.")
    else:
        label, heading = "Region", "Regional Polling"
        note = ("Same weighting methodology as the overall average, applied to "
                "region-specific polls.")
    return f"""<section id="regional">
  <h2>{heading}</h2>
  <div class="region-controls">
    <label for="regionSelect" style="font-weight:600;font-size:.8rem">{label}:</label>
    <select id="regionSelect" onchange="updateRegionalChart()">
      {options}
    </select>
  </div>
  <div class="chart-wrap"><canvas id="regionalChart"></canvas></div>
  <p class="section-note">{note}</p>
</section>"""


def build_html(
    national: list[dict],
    current_avg: dict,
    seat_proj: dict,
    seat_hist: list[dict],
    regional: dict[str, list[dict]],
    ridings: list[dict],
    prev_winners: dict[str, str] | None = None,
    cfg: Jurisdiction = FEDERAL,
) -> str:
    as_of = seat_proj.get("as_of", date.today().isoformat())

    # Current standings badges
    parties_avg = current_avg.get("parties", {})
    standings = ""
    for p in sorted(parties_avg, key=lambda x: -parties_avg[x]["mean"]):
        s = parties_avg[p]
        color = cfg.party_colors.get(p, "#888")
        standings += (
            f'<div class="standing-chip">'
            f'<span class="chip-badge" style="background:{color}">{p}</span>'
            f'<span class="chip-pct">{s["mean"]:.1f}%</span>'
            f'</div>'
        )

    # Embed all chart data as JSON
    def clean_rows(rows, keys):
        """Extract needed keys and coerce numeric strings to floats."""
        out = []
        for r in rows:
            row = {}
            for k in keys:
                v = r.get(k)
                if k == "date" or v is None or v == "":
                    row[k] = v
                elif isinstance(v, float):
                    row[k] = round(v, 2)
                elif isinstance(v, (int, bool)):
                    row[k] = v
                else:
                    try:
                        row[k] = round(float(v), 2)
                    except (ValueError, TypeError):
                        row[k] = v
            out.append(row)
        return out

    nat_keys = ["date"] + [f"{p}_{s}" for p in cfg.parties for s in ("mean", "std")]
    seat_keys = ["date"] + [f"{p}_{s}" for p in cfg.parties for s in ("mean", "low", "high")]
    reg_keys  = ["date"] + [f"{p}_{s}" for p in cfg.parties for s in ("mean", "std")]

    regional_clean = {r: clean_rows(rows, reg_keys) for r, rows in regional.items()}
    region_labels = {
        g: cfg.swing_group_labels.get(g, g) for g in regional_clean
    }

    data_js = json.dumps({
        "asOf": as_of,
        "partyColors": cfg.party_colors,
        "parties": list(cfg.parties),
        "seatEligible": list(cfg.seat_eligible),
        "majority": cfg.majority,
        "regionLabels": region_labels,
        "nationalPolling": clean_rows(national, nat_keys),
        "currentAverages": {
            p: {k: round(v, 2) if isinstance(v, float) else v
                for k, v in s.items()}
            for p, s in parties_avg.items()
        },
        "seatProjection": seat_proj.get("parties", {}),
        "seatHistory": clean_rows(seat_hist, seat_keys),
        "regionalPolling": regional_clean,
    }, separators=(",", ":"))

    riding_table = _riding_table_html(ridings, seat_proj, prev_winners, cfg)

    # Cross-links to the other jurisdictions' dashboards.
    siblings = "".join(
        f'<a href="../{j.docs_dir.name}/">{j.title}</a>'
        for j in ALL_JURISDICTIONS.values() if j.key != cfg.key
    )
    election_note = (
        f' · Election {cfg.election_date}' if cfg.election_date else ""
    )
    regional_section = _regional_section_html(cfg, region_labels)
    regional_nav = ""
    if regional_section:
        nav_label = "Language" if cfg.swing_axis == "language" else "Regional"
        regional_nav = f'<a href="#regional">{nav_label}</a>' 

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cfg.site_title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;background:#f0f2f5;color:#222}}
a{{color:inherit;text-decoration:none}}

/* ── Header ── */
header{{background:#1e2432;color:#fff;padding:14px 20px 0}}
.header-top{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
header h1{{font-size:1.2rem;font-weight:700}}
.updated{{font-size:0.75rem;opacity:.6}}
.standings{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 0}}
.standing-chip{{display:flex;align-items:center;gap:5px}}
.chip-badge{{padding:2px 7px;border-radius:4px;font-weight:700;font-size:0.78rem}}
.chip-pct{{font-size:0.85rem;font-weight:600}}
nav{{display:flex;gap:0;margin-top:12px;border-top:1px solid rgba(255,255,255,.1)}}
nav a{{padding:8px 16px;font-size:0.8rem;opacity:.7;border-bottom:2px solid transparent;transition:.15s}}
.nav-spacer{{flex:1}}
nav a:hover{{opacity:1;border-bottom-color:rgba(255,255,255,.4)}}

/* ── Layout ── */
main{{max-width:1100px;margin:0 auto;padding:20px 16px}}
section{{background:#fff;border-radius:8px;padding:20px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,.07)}}
section h2{{font-size:1rem;font-weight:700;margin-bottom:14px;color:#1e2432}}
.section-note{{font-size:0.75rem;color:#888;margin-top:8px}}

/* ── Chart containers ── */
.chart-wrap{{position:relative;height:320px}}
.chart-wrap-short{{position:relative;height:220px}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:700px){{.two-col{{grid-template-columns:1fr}}}}

/* ── Region selector ── */
.region-controls{{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap}}
.region-controls select{{padding:5px 10px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff}}

/* ── Seat summary bar ── */
.seat-summary{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}}
.seat-card{{background:#f7f8fa;border-radius:6px;padding:10px 14px;text-align:center;min-width:80px}}
.seat-card .party{{font-weight:700;font-size:0.8rem;padding:2px 8px;border-radius:4px;color:#fff;display:inline-block;margin-bottom:4px}}
.seat-card .mean{{font-size:1.4rem;font-weight:700;line-height:1.1}}
.seat-card .ci{{font-size:0.7rem;color:#888}}
.majority-marker{{align-self:center;border-left:2px solid #ddd;padding-left:12px;color:#666;font-size:0.8rem}}

/* ── Riding table (copied from generate_riding_table.py) ── */
.riding-summary{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}}
.summary-party{{text-align:center;min-width:60px}}
.summary-badge{{display:inline-block;border-radius:4px;padding:2px 8px;font-weight:700;font-size:0.8rem;color:#fff}}
.summary-seats{{font-size:1.5rem;font-weight:700;line-height:1.2;margin-top:3px}}
.summary-ci{{font-size:0.7rem;color:#777}}
.majority-note{{align-self:center;border-left:2px solid #ddd;padding-left:12px;color:#666;font-size:0.78rem;margin-left:auto}}
.controls{{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}}
.controls input{{padding:5px 10px;border:1px solid #ccc;border-radius:6px;font-size:13px;width:200px}}
.controls select{{padding:5px 9px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff}}
.result-count{{font-size:0.75rem;color:#888;margin-bottom:6px}}
.table-wrap{{overflow-x:auto;border-radius:6px;border:1px solid #eee}}
table{{width:100%;border-collapse:collapse}}
thead th{{background:#1e2432;color:#fff;padding:7px 9px;text-align:left;font-size:0.75rem;font-weight:600;letter-spacing:.03em;white-space:nowrap;cursor:pointer;user-select:none}}
thead th:hover{{background:#2d3450}}
thead th.sorted-asc::after{{content:" ↑";opacity:.7}}
thead th.sorted-desc::after{{content:" ↓";opacity:.7}}
tbody tr{{border-bottom:1px solid #eef0f3}}
tbody tr:last-child{{border-bottom:none}}
tr.riding-row:hover{{background:#f0f4ff}}
tr.province-header td{{background:#f0f2f6;padding:5px 9px;font-weight:600;font-size:0.78rem}}
.prov-name{{margin-right:12px}}
.pseat{{font-size:0.73rem;font-weight:700;margin-right:6px}}
td{{padding:5px 9px;vertical-align:middle}}
.riding-name{{font-weight:500;min-width:170px}}
.prov-code{{color:#888;font-size:0.73rem;font-weight:600;text-align:center;white-space:nowrap}}
.winner-badge{{display:inline-block;padding:1px 7px;border-radius:4px;font-weight:700;font-size:0.77rem;color:#fff}}
.comp-label{{font-size:0.7rem;padding:1px 6px;border-radius:10px;font-weight:600;white-space:nowrap}}
.comp-safe{{background:#e8f5e9;color:#2e7d32}}
.comp-likely{{background:#fff8e1;color:#f57f17}}
.comp-tossup{{background:#fce4ec;color:#c62828}}
.change-badge{{display:inline-flex;align-items:center;gap:2px;margin-left:5px;vertical-align:middle}}
.change-from,.change-to{{font-size:0.65rem;padding:0px 4px;border-radius:3px;color:#fff;font-weight:700}}
.change-arrow{{font-size:0.65rem;color:#888}}
tr.riding-row.changed{{background:#fffbeb}}
tr.riding-row.changed:hover{{background:#fef3c7}}
.prob-td{{min-width:75px;padding:3px 7px}}
.prob-cell{{display:flex;align-items:center;gap:4px}}
.bar-wrap{{flex:1;height:7px;background:#eee;border-radius:4px;overflow:hidden;min-width:36px}}
.bar{{height:100%;border-radius:4px}}
.prob-label{{font-size:0.7rem;color:#555;width:26px;text-align:right;flex-shrink:0}}
tr.hidden{{display:none}}
@media(max-width:700px){{.prob-td{{display:none}}}}
</style>
</head>
<body>
<header>
  <div class="header-top">
    <h1>{cfg.site_title}</h1>
    <span class="updated">Updated {as_of}{election_note}</span>
  </div>
  <div class="standings">{standings}</div>
  <nav>
    <a href="#polling">Polling Average</a>
    <a href="#seats">Seat Projection</a>
    {regional_nav}
    <a href="#ridings">Riding Projections</a>
    <span class="nav-spacer"></span>{siblings}<a href="../">All trackers</a>
  </nav>
</header>

<main>

<!-- ── 1. National Polling ── -->
<section id="polling">
  <h2>Polling Average</h2>
  <div class="chart-wrap"><canvas id="pollingChart"></canvas></div>
  <p class="section-note">Weighted average (age decay × sample size × pollster rating). Shaded bands = 95% confidence interval.</p>
</section>

<!-- ── 2. Seat Projection ── -->
<section id="seats">
  <h2>Seat Projection — {cfg.title}</h2>
  <div class="seat-summary" id="seatSummary"></div>
  <div class="two-col">
    <div>
      <div style="font-size:.8rem;font-weight:600;color:#555;margin-bottom:8px">Current projection</div>
      <div class="chart-wrap-short"><canvas id="seatBarChart"></canvas></div>
    </div>
    <div>
      <div style="font-size:.8rem;font-weight:600;color:#555;margin-bottom:8px">Seats over time</div>
      <div class="chart-wrap-short"><canvas id="seatHistoryChart"></canvas></div>
    </div>
  </div>
  <p class="section-note">{cfg.n_simulations:,} Monte Carlo simulations. Bars show mean projected seats; error bars show 95% CI. Dashed line = majority ({cfg.majority} seats).</p>
</section>

<!-- ── 3. Regional Polling ── -->
{regional_section}

<!-- ── 4. Riding Projections ── -->
<section id="ridings">
  <h2>Riding Projections</h2>
  {riding_table}
</section>

</main>

<script>
const DATA = {data_js};

const COLORS = DATA.partyColors;
const PARTIES = DATA.parties;
const CI_ALPHA = "28";  // hex alpha for CI bands

// ── helpers ───────────────────────────────────────────────────────────────────
function hexToRgba(hex, alpha) {{
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return `rgba(${{r}},${{g}},${{b}},${{alpha}})`;
}}

function makePollingDatasets(rows, parties) {{
  const dates = rows.map(r => r.date);
  const datasets = [];
  for (const p of parties) {{
    const mean  = rows.map(r => r[p+"_mean"]);
    const std   = rows.map(r => r[p+"_std"] || 0);
    const low   = mean.map((m,i) => m != null ? +(m - 1.96*std[i]).toFixed(2) : null);
    const high  = mean.map((m,i) => m != null ? +(m + 1.96*std[i]).toFixed(2) : null);
    const color = COLORS[p] || "#888";
    datasets.push(
      {{ label: p+"_ci_low",  data: low,  borderWidth:0, pointRadius:0, fill:"+1",
         backgroundColor: hexToRgba(color, 0.12), tension:0.3 }},
      {{ label: p+"_ci_high", data: high, borderWidth:0, pointRadius:0, fill:false,
         backgroundColor: hexToRgba(color, 0.12), tension:0.3 }},
      {{ label: p, data: mean, borderColor: color, backgroundColor: hexToRgba(color,0.1),
         borderWidth:2, pointRadius:0, tension:0.3, fill:false }}
    );
  }}
  return {{ labels: dates, datasets }};
}}

const legendFilter = (item) => !item.text.endsWith("_ci_low") && !item.text.endsWith("_ci_high");
const baseOptions = (yLabel) => ({{
  responsive: true, maintainAspectRatio: false,
  interaction: {{ mode:"index", intersect:false }},
  plugins: {{
    legend: {{ labels: {{ filter: legendFilter, boxWidth:12, font:{{size:11}} }} }},
    tooltip: {{
      filter: (item) => !item.dataset.label.includes("_ci_"),
      callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y?.toFixed(1)}}%` }}
    }}
  }},
  scales: {{
    x: {{ ticks:{{ maxTicksLimit:8, font:{{size:10}} }}, grid:{{ display:false }} }},
    y: {{ min:0, ticks:{{ callback: v => v+"%", font:{{size:10}} }}, title:{{ display:true, text:yLabel, font:{{size:10}} }} }}
  }}
}});

// ── 1. National Polling Chart ──────────────────────────────────────────────
(function() {{
  if (!DATA.nationalPolling.length) return;
  const {{ labels, datasets }} = makePollingDatasets(DATA.nationalPolling, PARTIES);
  new Chart(document.getElementById("pollingChart"), {{
    type:"line", data:{{ labels, datasets }}, options: baseOptions("Vote share (%)")
  }});
}})();

// ── 2. Seat projection ────────────────────────────────────────────────────
(function() {{
  const proj = DATA.seatProjection;
  const majorParties = DATA.seatEligible.filter(
    p => proj[p] && (proj[p].high95 > 0 || proj[p].mean_seats >= 0.5)
  );
  const summaryEl = document.getElementById("seatSummary");

  // Summary cards
  for (const p of majorParties) {{
    const s = proj[p]; if (!s) continue;
    const color = COLORS[p] || "#888";
    summaryEl.innerHTML +=
      `<div class="seat-card">
        <span class="party" style="background:${{color}}">${{p}}</span>
        <div class="mean">${{Math.round(s.mean_seats)}}</div>
        <div class="ci">[${{s.low95}}–${{s.high95}}]</div>
      </div>`;
  }}
  summaryEl.innerHTML += `<div class="majority-marker">Majority<br>${{DATA.majority}} seats</div>`;

  // Bar chart (horizontal)
  const sorted = majorParties.filter(p => proj[p]).sort((a,b) => proj[a].mean_seats - proj[b].mean_seats);
  new Chart(document.getElementById("seatBarChart"), {{
    type:"bar",
    data:{{
      labels: sorted,
      datasets:[{{
        data: sorted.map(p => proj[p].mean_seats),
        backgroundColor: sorted.map(p => COLORS[p] || "#888"),
        borderRadius: 4,
      }}]
    }},
    options:{{
      indexAxis:"y", responsive:true, maintainAspectRatio:false,
      plugins:{{
        legend:{{ display:false }},
        tooltip:{{ callbacks:{{ label: ctx => ` ${{ctx.parsed.x.toFixed(0)}} seats` }} }},
        annotation: undefined
      }},
      scales:{{
        x:{{ min:0, max:260, ticks:{{font:{{size:10}}}},
             title:{{display:true,text:"Projected seats",font:{{size:10}}}},
             grid:{{color:"rgba(0,0,0,.05)"}} }},
        y:{{ ticks:{{font:{{size:11,weight:"bold"}}}} }}
      }}
    }}
  }});

  // History chart
  const hist = DATA.seatHistory;
  if (hist.length === 0) {{
    document.getElementById("seatHistoryChart").closest("div").innerHTML =
      '<p style="padding:40px;text-align:center;color:#aaa;font-size:0.85rem">Seat history will appear after the first weekly update.</p>';
    return;
  }}
  const histDates = hist.map(r => r.date);
  const histDatasets = [];
  for (const p of ["LPC","CPC","NDP","BQ"]) {{
    const color = COLORS[p];
    const mean = hist.map(r => r[p+"_mean"] != null ? +r[p+"_mean"] : null);
    const low  = hist.map(r => r[p+"_low"]  != null ? +r[p+"_low"]  : null);
    const high = hist.map(r => r[p+"_high"] != null ? +r[p+"_high"] : null);
    histDatasets.push(
      {{ label:p+"_ci_low",  data:low,  borderWidth:0, pointRadius:0, fill:"+1", backgroundColor:hexToRgba(color,0.12), tension:0.3 }},
      {{ label:p+"_ci_high", data:high, borderWidth:0, pointRadius:0, fill:false, backgroundColor:hexToRgba(color,0.12), tension:0.3 }},
      {{ label:p, data:mean, borderColor:color, backgroundColor:hexToRgba(color,0.1), borderWidth:2, pointRadius: hist.length===1?5:0, tension:0.3, fill:false }}
    );
  }}
  const seatOpts = {{
    responsive:true, maintainAspectRatio:false,
    interaction:{{mode:"index",intersect:false}},
    plugins:{{
      legend:{{ labels:{{ filter:legendFilter, boxWidth:12, font:{{size:10}} }} }},
      tooltip:{{ filter: item => !item.dataset.label.includes("_ci_"),
                 callbacks:{{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y?.toFixed(0)}} seats` }} }}
    }},
    scales:{{
      x:{{ ticks:{{maxTicksLimit:6,font:{{size:10}}}}, grid:{{display:false}} }},
      y:{{ min:0, ticks:{{font:{{size:10}}}}, title:{{display:true,text:"Seats",font:{{size:10}}}} }}
    }}
  }};
  new Chart(document.getElementById("seatHistoryChart"), {{
    type:"line", data:{{labels:histDates, datasets:histDatasets}}, options:seatOpts
  }});
}})();

// ── 3. Regional chart ─────────────────────────────────────────────────────
let regionalChart = null;
function updateRegionalChart() {{
  const sel = document.getElementById("regionSelect");
  if (!sel) return;              // jurisdiction publishes no regional crosstabs
  const rows = DATA.regionalPolling[sel.value] || [];
  const {{ labels, datasets }} = makePollingDatasets(rows, DATA.seatEligible);
  if (regionalChart) {{
    regionalChart.data.labels = labels;
    regionalChart.data.datasets = datasets;
    regionalChart.update();
  }} else {{
    regionalChart = new Chart(document.getElementById("regionalChart"), {{
      type:"line", data:{{labels, datasets}},
      options: baseOptions("Vote share (%)")
    }});
  }}
}}
updateRegionalChart();

// ── 4. Riding table ───────────────────────────────────────────────────────
let sortCol = -1, sortAsc = true;

function filterTable() {{
  const search  = document.getElementById("search").value.toLowerCase();
  const provF   = document.getElementById("prov-filter").value;
  const winF    = document.getElementById("winner-filter").value;
  const compF   = document.getElementById("comp-filter").value;
  const changeF = document.getElementById("change-filter").value;
  const rows    = document.querySelectorAll("tr.riding-row");
  let visible   = 0;
  rows.forEach(r => {{
    const margin = parseFloat(r.dataset.margin || "100");
    let compMatch = true;
    if (compF === "tossup") compMatch = margin < 5;
    else if (compF === "likely") compMatch = margin >= 5 && margin < 15;
    else if (compF === "safe") compMatch = margin >= 15;
    const show = (
      (!search  || (r.dataset.riding||"").includes(search)) &&
      (!provF   || (r.dataset.province||"").includes(provF)) &&
      (!winF    || r.dataset.winner === winF) &&
      (!changeF || r.classList.contains("changed")) &&
      compMatch
    );
    r.classList.toggle("hidden", !show);
    if (show) visible++;
  }});
  document.querySelectorAll("tr.province-header").forEach(ph => {{
    const prov = ph.dataset.province;
    const hasVisible = [...rows].some(r => r.dataset.province === prov && !r.classList.contains("hidden"));
    ph.classList.toggle("hidden", !hasVisible);
  }});
  document.getElementById("result-count").textContent = `Showing ${{visible}} of ${{rows.length}} ridings`;
}}

function sortTable(col) {{
  const ths = document.querySelectorAll("thead th");
  ths.forEach(th => th.classList.remove("sorted-asc","sorted-desc"));
  sortAsc = (sortCol === col) ? !sortAsc : true;
  sortCol = col;
  ths[col].classList.add(sortAsc ? "sorted-asc" : "sorted-desc");
  const tbody = document.getElementById("table-body");
  const rows = [...tbody.querySelectorAll("tr.riding-row")];
  const provHeaders = [...tbody.querySelectorAll("tr.province-header")];
  rows.sort((a,b) => {{
    let va = a.cells[col]?.textContent.trim() ?? "";
    let vb = b.cells[col]?.textContent.trim() ?? "";
    if (col >= 4) {{ va = parseFloat(va)||0; vb = parseFloat(vb)||0; return sortAsc ? va-vb : vb-va; }}
    return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});
  provHeaders.forEach(ph => {{ if (col > 0) ph.classList.add("hidden"); else ph.classList.remove("hidden"); }});
  if (col === 0) {{
    const byProv = {{}};
    rows.forEach(r => {{ const p = r.dataset.province; if (!byProv[p]) byProv[p]=[]; byProv[p].push(r); }});
    tbody.innerHTML = "";
    provHeaders.forEach(ph => {{ tbody.appendChild(ph); (byProv[ph.dataset.province]||[]).forEach(r=>tbody.appendChild(r)); }});
  }} else {{
    tbody.innerHTML = "";
    provHeaders.forEach(ph => tbody.appendChild(ph));
    rows.forEach(r => tbody.appendChild(r));
  }}
  filterTable();
}}
filterTable();
</script>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def build_landing_page() -> str:
    """A small index listing every jurisdiction's dashboard."""
    cards = ""
    for j in ALL_JURISDICTIONS.values():
        current = load_current_averages(j).get("parties", {})
        seats = load_seat_projection(j)
        as_of = seats.get("as_of") or load_current_averages(j).get("as_of", "")

        chips = "".join(
            f'<span class="chip" style="background:{j.party_colors.get(p, "#888")}">'
            f'{p} {s["mean"]:.0f}%</span>'
            for p, s in sorted(current.items(), key=lambda x: -x[1]["mean"])
            if p in j.seat_eligible
        )
        when = f"Election {j.election_date}" if j.election_date else "Date not yet set"
        cards += f"""
  <a class="card" href="{j.docs_dir.name}/">
    <h2>{j.site_title}</h2>
    <div class="meta">{when}{" · updated " + as_of if as_of else ""}</div>
    <div class="chips">{chips}</div>
  </a>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polling Trackers</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f0f2f5;color:#222;padding:40px 16px}}
main{{max-width:720px;margin:0 auto}}
h1{{font-size:1.4rem;margin-bottom:6px}}
.sub{{color:#666;font-size:.85rem;margin-bottom:24px}}
.card{{display:block;background:#fff;border-radius:8px;padding:20px;margin-bottom:16px;
      box-shadow:0 1px 4px rgba(0,0,0,.07);text-decoration:none;color:inherit;transition:.15s}}
.card:hover{{box-shadow:0 3px 12px rgba(0,0,0,.13);transform:translateY(-1px)}}
.card h2{{font-size:1.05rem;margin-bottom:4px}}
.meta{{font-size:.78rem;color:#777;margin-bottom:10px}}
.chips{{display:flex;gap:6px;flex-wrap:wrap}}
.chip{{color:#fff;font-weight:700;font-size:.72rem;padding:2px 7px;border-radius:4px}}
footer{{margin-top:28px;font-size:.75rem;color:#888;text-align:center}}
</style>
</head>
<body>
<main>
  <h1>Polling Trackers</h1>
  <div class="sub">Weighted polling averages and Monte Carlo seat projections.</div>
  {cards}
  <footer>Built from public polling. Methodology after 338Canada.</footer>
</main>
</body>
</html>"""


def main(cfg: Jurisdiction = FEDERAL) -> None:
    docs_dir = cfg.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    Path("docs").mkdir(exist_ok=True)
    (Path("docs") / ".nojekyll").touch()

    print("  Loading data …")
    national     = load_national_rolling(cfg)
    current      = load_current_averages(cfg)
    seat_proj    = load_seat_projection(cfg)
    ridings      = load_riding_projections(cfg)
    prev_winners = load_prev_winners(cfg)

    print("  Updating seat history …")
    if seat_proj:
        update_seat_history(seat_proj, cfg)
    seat_hist = load_seat_history(cfg)

    print("  Computing regional rolling averages (90 days) …")
    regional = compute_regional_rolling(90, cfg)

    print("  Building HTML …")
    html = build_html(
        national, current, seat_proj, seat_hist, regional, ridings, prev_winners, cfg
    )
    out = docs_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"  Saved → {out}  ({len(html)//1024}KB)")

    landing = Path("docs/index.html")
    landing.write_text(build_landing_page(), encoding="utf-8")
    print(f"  Saved → {landing}")


if __name__ == "__main__":
    import argparse
    import jurisdictions

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jurisdiction", default="federal", choices=list(jurisdictions.ALL))
    main(jurisdictions.get(ap.parse_args().jurisdiction))
