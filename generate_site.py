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

from polling_model import (
    PARTIES,
    age_weight,
    get_pollster_rating,
    parse_date,
    sample_weight,
    weighted_stats,
)

DOCS_DIR          = Path("docs")
SEAT_HISTORY_CSV  = Path("seat_history.csv")
NATIONAL_CSV      = Path("polling_average.csv")
REGIONAL_CSV      = Path("regional_polls.csv")
CURRENT_JSON      = Path("current_average.json")
SEAT_JSON         = Path("seat_projection.json")
RIDING_CSV        = Path("riding_projections.csv")

PARTY_COLORS = {
    "LPC": "#D71920", "CPC": "#1A4782", "NDP": "#F4831F",
    "BQ":  "#00A0C6", "GPC": "#3D9B35", "PPC": "#4B0082",
}
REGION_LABELS = {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia",
    "AB": "Alberta", "MB_SK": "Man./Sask.", "Atlantic": "Atlantic",
}
PROVINCE_ORDER = [
    "Newfoundland and Labrador", "Prince Edward Island", "Nova Scotia",
    "New Brunswick", "Quebec", "Ontario", "Manitoba", "Saskatchewan",
    "Alberta", "British Columbia", "Northwest Territories", "Nunavut", "Yukon",
]
PROVINCE_SHORT = {
    "Newfoundland and Labrador": "NL", "Prince Edward Island": "PE",
    "Nova Scotia": "NS", "New Brunswick": "NB", "Quebec": "QC",
    "Ontario": "ON", "Manitoba": "MB", "Saskatchewan": "SK",
    "Alberta": "AB", "British Columbia": "BC",
    "Northwest Territories": "NT", "Nunavut": "NU", "Yukon": "YT",
}


# ── Data loading ──────────────────────────────────────────────────────────────

def load_national_rolling() -> list[dict]:
    """Read polling_average.csv → list of {date, LPC_mean, LPC_std, ...}."""
    if not NATIONAL_CSV.exists():
        return []
    with open(NATIONAL_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_current_averages() -> dict:
    if not CURRENT_JSON.exists():
        return {}
    with open(CURRENT_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_seat_projection() -> dict:
    if not SEAT_JSON.exists():
        return {}
    with open(SEAT_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_riding_projections() -> list[dict]:
    if not RIDING_CSV.exists():
        return []
    with open(RIDING_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Seat history ──────────────────────────────────────────────────────────────

def update_seat_history(seat_data: dict) -> None:
    """Append today's seat projection to seat_history.csv (if not already present)."""
    today = date.today().isoformat()
    parties_data = seat_data.get("parties", {})

    fieldnames = ["date"]
    for p in PARTIES:
        fieldnames += [f"{p}_mean", f"{p}_low", f"{p}_high"]

    # Read existing rows
    existing = []
    if SEAT_HISTORY_CSV.exists():
        with open(SEAT_HISTORY_CSV, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    # Don't duplicate today
    if any(r["date"] == today for r in existing):
        return

    new_row = {"date": today}
    for p in PARTIES:
        stats = parties_data.get(p, {})
        new_row[f"{p}_mean"] = stats.get("mean_seats", "")
        new_row[f"{p}_low"]  = stats.get("low95", "")
        new_row[f"{p}_high"] = stats.get("high95", "")

    existing.append(new_row)

    with open(SEAT_HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)


def load_seat_history() -> list[dict]:
    if not SEAT_HISTORY_CSV.exists():
        return []
    with open(SEAT_HISTORY_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Regional rolling averages ─────────────────────────────────────────────────

def compute_regional_rolling(days: int = 90) -> dict[str, list[dict]]:
    """
    Compute 90-day rolling weighted average per region from regional_polls.csv.
    Returns {region: [{date, LPC_mean, LPC_std, ...}, ...]}.
    """
    if not REGIONAL_CSV.exists():
        return {}

    # Load and parse regional polls
    polls_by_region: dict[str, list[dict]] = defaultdict(list)
    with open(REGIONAL_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = parse_date(row.get("date", ""))
            if d is None:
                continue
            region = row.get("region", "")
            if not region:
                continue
            try:
                n = int(row.get("sample_size") or 1000)
            except ValueError:
                n = 1000
            pcts = {}
            for p in PARTIES:
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
                aw = age_weight(poll["date"], ref)
                sw = sample_weight(poll["n"])
                pr = get_pollster_rating(poll["firm"])
                w  = aw * sw * pr
                for p, pct in poll["pcts"].items():
                    party_vw[p].append((pct, w))

            row = {"date": ref.isoformat()}
            for p in PARTIES:
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

def _riding_table_html(ridings: list[dict], seat_data: dict) -> str:
    """Return the riding projections table + controls HTML fragment."""
    parties_data = seat_data.get("parties", {})
    as_of = seat_data.get("as_of", "")
    n_sims = seat_data.get("simulations", 10_000)

    # Summary cells
    summary_cells = ""
    for p in sorted(parties_data, key=lambda x: -parties_data[x]["mean_seats"]):
        s = parties_data[p]
        color = PARTY_COLORS.get(p, "#888")
        summary_cells += (
            f'<div class="summary-party">'
            f'<div class="summary-badge" style="background:{color}">{p}</div>'
            f'<div class="summary-seats">{s["mean_seats"]:.0f}</div>'
            f'<div class="summary-ci">[{s["low95"]}–{s["high95"]}]</div>'
            f'</div>'
        )

    # Group ridings by province
    by_province: dict[str, list] = {p: [] for p in PROVINCE_ORDER}
    for r in ridings:
        prov = r.get("province", "")
        if prov in by_province:
            by_province[prov].append(r)

    table_rows = ""
    for prov in PROVINCE_ORDER:
        prov_ridings = sorted(by_province.get(prov, []), key=lambda r: r["riding_name"])
        if not prov_ridings:
            continue
        short = PROVINCE_SHORT.get(prov, prov[:2])

        prov_seats: dict[str, int] = {}
        for r in prov_ridings:
            w = r["projected_winner"]
            prov_seats[w] = prov_seats.get(w, 0) + 1
        seat_summary = "  ".join(
            f'<span class="pseat" style="color:{PARTY_COLORS.get(p,"#888")}">{p}&nbsp;{n}</span>'
            for p, n in sorted(prov_seats.items(), key=lambda x: -x[1])
        )
        table_rows += (
            f'<tr class="province-header" data-province="{prov}">'
            f'<td colspan="10"><span class="prov-name">{prov}</span>'
            f'<span class="prov-seats">{seat_summary}</span></td></tr>\n'
        )

        for r in prov_ridings:
            winner = r["projected_winner"]
            probs  = {p: float(r.get(f"P_{p}", 0) or 0) for p in PARTIES}
            top_p  = probs.get(winner, 0)
            w_color = PARTY_COLORS.get(winner, "#888")

            if top_p < 0.60:
                comp_cls, comp_lbl = "comp-tossup", "Toss-up"
            elif top_p < 0.80:
                comp_cls, comp_lbl = "comp-likely", "Likely"
            else:
                comp_cls, comp_lbl = "comp-safe",   "Safe"

            badge = (
                f'<span class="winner-badge" style="background:{w_color}">{winner}</span>'
            )
            party_cells = ""
            for p in PARTIES:
                prob = probs.get(p, 0)
                if prob < 0.005:
                    party_cells += '<td class="prob-td"></td>\n'
                else:
                    pct = round(prob * 100, 1)
                    color = PARTY_COLORS.get(p, "#aaa")
                    party_cells += (
                        f'<td class="prob-td">'
                        f'<div class="prob-cell">'
                        f'<div class="bar-wrap"><div class="bar" style="width:{min(pct,100):.1f}%;background:{color}"></div></div>'
                        f'<span class="prob-label">{pct:.0f}%</span>'
                        f'</div></td>\n'
                    )

            table_rows += (
                f'<tr class="riding-row" '
                f'data-riding="{r["riding_name"].lower()}" '
                f'data-province="{prov.lower()}" '
                f'data-winner="{winner}" '
                f'data-top="{top_p:.3f}">\n'
                f'  <td class="riding-name">{r["riding_name"]}</td>\n'
                f'  <td class="prov-code">{short}</td>\n'
                f'  <td class="winner-td">{badge}</td>\n'
                f'  <td class="comp-td"><span class="comp-label {comp_cls}">{comp_lbl}</span></td>\n'
                + party_cells +
                f'</tr>\n'
            )

    province_options = "".join(
        f'<option value="{p.lower()}">{p}</option>'
        for p in PROVINCE_ORDER
        if by_province.get(p)
    )

    return f"""
<div class="riding-summary">
  {summary_cells}
  <div class="majority-note">Majority: 172 seats<br><small>{n_sims:,} simulations · as of {as_of}</small></div>
</div>
<div class="controls">
  <input type="text" id="search" placeholder="Search riding…" oninput="filterTable()">
  <select id="prov-filter" onchange="filterTable()">
    <option value="">All provinces</option>
    {province_options}
  </select>
  <select id="winner-filter" onchange="filterTable()">
    <option value="">All parties</option>
    {"".join(f'<option value="{p}">{p}</option>' for p in PARTIES)}
  </select>
  <select id="comp-filter" onchange="filterTable()">
    <option value="">All races</option>
    <option value="tossup">Toss-ups (&lt;60%)</option>
    <option value="likely">Likely (60–80%)</option>
    <option value="safe">Safe (≥80%)</option>
  </select>
</div>
<div class="result-count" id="result-count"></div>
<div class="table-wrap">
  <table id="riding-table">
    <thead>
      <tr>
        <th onclick="sortTable(0)">Riding</th>
        <th onclick="sortTable(1)">Prov</th>
        <th onclick="sortTable(2)">Winner</th>
        <th onclick="sortTable(3)">Confidence</th>
        <th>LPC</th><th>CPC</th><th>NDP</th><th>BQ</th><th>GPC</th><th>PPC</th>
      </tr>
    </thead>
    <tbody id="table-body">
      {table_rows}
    </tbody>
  </table>
</div>"""


def build_html(
    national: list[dict],
    current_avg: dict,
    seat_proj: dict,
    seat_hist: list[dict],
    regional: dict[str, list[dict]],
    ridings: list[dict],
) -> str:
    as_of = seat_proj.get("as_of", date.today().isoformat())

    # Current standings badges
    parties_avg = current_avg.get("parties", {})
    standings = ""
    for p in sorted(parties_avg, key=lambda x: -parties_avg[x]["mean"]):
        s = parties_avg[p]
        color = PARTY_COLORS.get(p, "#888")
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

    nat_keys = ["date"] + [f"{p}_{s}" for p in PARTIES for s in ("mean", "std")]
    seat_keys = ["date"] + [f"{p}_{s}" for p in PARTIES for s in ("mean", "low", "high")]
    reg_keys  = ["date"] + [f"{p}_{s}" for p in PARTIES for s in ("mean", "std")]

    regional_clean = {r: clean_rows(rows, reg_keys) for r, rows in regional.items()}

    data_js = json.dumps({
        "asOf": as_of,
        "partyColors": PARTY_COLORS,
        "parties": PARTIES,
        "regionLabels": REGION_LABELS,
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

    riding_table = _riding_table_html(ridings, seat_proj)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canadian Federal Polling Tracker</title>
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
    <h1>🍁 Canadian Federal Polling Tracker</h1>
    <span class="updated">Updated {as_of}</span>
  </div>
  <div class="standings">{standings}</div>
  <nav>
    <a href="#polling">Polling Average</a>
    <a href="#seats">Seat Projection</a>
    <a href="#regional">Regional</a>
    <a href="#ridings">Riding Projections</a>
  </nav>
</header>

<main>

<!-- ── 1. National Polling ── -->
<section id="polling">
  <h2>National Polling Average</h2>
  <div class="chart-wrap"><canvas id="pollingChart"></canvas></div>
  <p class="section-note">Weighted average (age decay × sample size × pollster rating). Shaded bands = 95% confidence interval.</p>
</section>

<!-- ── 2. Seat Projection ── -->
<section id="seats">
  <h2>Seat Projection — 46th Canadian Federal Election</h2>
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
  <p class="section-note">10,000 Monte Carlo simulations. Bars show mean projected seats; error bars show 95% CI. Dashed line = majority (172 seats).</p>
</section>

<!-- ── 3. Regional Polling ── -->
<section id="regional">
  <h2>Regional Polling</h2>
  <div class="region-controls">
    <label for="regionSelect" style="font-weight:600;font-size:.8rem">Region:</label>
    <select id="regionSelect" onchange="updateRegionalChart()">
      {"".join(f'<option value="{k}">{v}</option>' for k, v in REGION_LABELS.items())}
    </select>
  </div>
  <div class="chart-wrap"><canvas id="regionalChart"></canvas></div>
  <p class="section-note">Same weighting methodology as national average, applied to region-specific polls.</p>
</section>

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
  const majorParties = ["LPC","CPC","NDP","BQ","GPC"];
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
  summaryEl.innerHTML += `<div class="majority-marker">Majority<br>172 seats</div>`;

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
  const region = document.getElementById("regionSelect").value;
  const rows = DATA.regionalPolling[region] || [];
  const {{ labels, datasets }} = makePollingDatasets(rows, ["LPC","CPC","NDP","BQ"]);
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
  const search = document.getElementById("search").value.toLowerCase();
  const provF  = document.getElementById("prov-filter").value;
  const winF   = document.getElementById("winner-filter").value;
  const compF  = document.getElementById("comp-filter").value;
  const rows   = document.querySelectorAll("tr.riding-row");
  let visible  = 0;
  rows.forEach(r => {{
    const top = parseFloat(r.dataset.top || "1");
    let compMatch = true;
    if (compF === "tossup") compMatch = top < 0.60;
    else if (compF === "likely") compMatch = top >= 0.60 && top < 0.80;
    else if (compF === "safe") compMatch = top >= 0.80;
    const show = (
      (!search || (r.dataset.riding||"").includes(search)) &&
      (!provF  || (r.dataset.province||"").includes(provF)) &&
      (!winF   || r.dataset.winner === winF) &&
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

def main() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    (DOCS_DIR / ".nojekyll").touch()

    print("  Loading data …")
    national  = load_national_rolling()
    current   = load_current_averages()
    seat_proj = load_seat_projection()
    ridings   = load_riding_projections()

    print("  Updating seat history …")
    if seat_proj:
        update_seat_history(seat_proj)
    seat_hist = load_seat_history()

    print("  Computing regional rolling averages (90 days) …")
    regional = compute_regional_rolling()

    print("  Building HTML …")
    html = build_html(national, current, seat_proj, seat_hist, regional, ridings)
    out = DOCS_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"  Saved → {out}  ({len(html)//1024}KB)")


if __name__ == "__main__":
    main()
