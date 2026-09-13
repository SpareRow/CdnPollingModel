#!/usr/bin/env python3
"""
Build the Quebec riding baseline: qc/riding_results_2022.csv

One-off build script, like build_elasticity.py. It is NOT part of the weekly
pipeline — it writes a CSV that gets committed, so CI never needs xlrd or a
network round-trip to Élections Québec.

    python3 build_qc_baseline.py

Three sources, all fetched live:

  1. 2022 results — Élections Québec open data, one clean JSON with per-candidate
     votes for all 125 divisions. Verified to reproduce the official result
     exactly (CAQ 40.98%/90, QS 15.43%/11, PQ 14.61%/3, PLQ 14.37%/21, PCQ 12.91%/0).

  2. Francophone share — the 2021 Census profiles Élections Québec publishes for
     the *new* 127-division map. This drives the language swing axis.

  3. Region — Wikipedia's district list, used only for grouping rows in the
     dashboard table.

The wrinkle is that Quebec redistributed for 2026: 125 -> 127 divisions, and the
division codes were entirely renumbered (Abitibi-Est 648 -> 689), so codes cannot
join the two maps. Names can: 119 of 127 match exactly once accents and dashes
are normalised. The remaining 8 are handled explicitly below.

Known limitation: 39 divisions had boundary changes that we do not adjust for —
a name-matched division carries its 2022 shares over unmodified. Doing better
would need poll-by-poll results and a geospatial join against both shapefiles.
That error is second-order next to the CAQ-collapse and language-baseline
uncertainties, but it does mean individual marginal divisions are shakier than
the topline seat count.
"""

import csv
import html as html_mod
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

RESULTS_2022_URL = (
    "https://donnees.electionsquebec.qc.ca/production/provincial/resultats/"
    "archives/gen2022-10-03/resultats.json"
)
DIVISIONS_2026_URL = (
    "https://donnees.electionsquebec.qc.ca/autres/provincial/liste_circonscriptions2026.csv"
)
CENSUS_XLS_URL = (
    "https://docs.electionsquebec.qc.ca/PRO/6a58edde4eab9/"
    "statistiques-recensement-2021-CEP2026.xls"
)
WIKI_DISTRICTS_URL = "https://en.wikipedia.org/wiki/List_of_Quebec_provincial_electoral_districts"

OUTPUT_CSV = Path("qc/riding_results_2022.csv")
CACHE_DIR = Path(".cache/qc")

PARTIES = ["CAQ", "PQ", "PLQ", "QS", "PCQ", "OTH"]

# Élections Québec party abbreviations -> our codes. Anything unmatched
# (independents, micro-parties) falls into OTH rather than being dropped.
PARTY_MAP = {
    "C.A.Q.-E.F.L.": "CAQ",
    "P.L.Q./Q.L.P.": "PLQ",
    "P.Q.": "PQ",
    "Q.S.": "QS",
    "P.C.Q-E.E.D.": "PCQ",
}

# 2026 division name -> its 2022 predecessor. Wikipedia's "Constituency name
# changes" table lists only the first five; Johnson is absent from it because
# Johnson simultaneously donated territory to a new division, but the remainder
# was renamed Daniel-Johnson and is that division's natural predecessor.
RENAMES = {
    "Arthabaska-L'Érable": "Arthabaska",
    "Pierre-Laporte": "Laporte",
    "Matane-Matapédia-Mitis": "Matane-Matapédia",
    "Rivière-du-Loup–Témiscouata–Les Basques": "Rivière-du-Loup-Témiscouata",
    "Vimont-Auteuil": "Vimont",
    "Daniel-Johnson": "Johnson",
}

# The two genuinely new divisions, with the 2022 divisions each was carved from.
# Donors are weighted by their 2022 registered electors — crude, since only part
# of each donor was transferred, but there is no published transposition.
NEW_DIVISIONS = {
    "Bellefeuille": ["Saint-Jérôme", "Mirabel", "Argenteuil"],
    "Marie-Lacoste-Gérin-Lajoie": ["Johnson", "Nicolet-Bécancour", "Drummond–Bois-Francs"],
}

# franco_pct weights a division between the francophone and non-francophone
# swing groups, so it needs to describe *voting behaviour*, not just mother
# tongue. Ungava is 31% French by the census, but the remainder is overwhelmingly
# Inuit and Cree — voters who behave nothing like Montreal anglophones. Left
# uncorrected it would be handed the strongly PLQ non-francophone swing. Give it
# the provincial average so it moves with the province, not the West Island.
FRANCO_SWING_OVERRIDES = {
    "Ungava": 80.8,   # census says 31.1%
}


def canonical_region(raw: str) -> str:
    """
    Tidy Wikipedia's region cell into a single canonical region name.

    Two quirks to undo: the cells use non-breaking spaces, and a division
    spanning two regions has them joined with no separator at all
    ("Laurentidesand Lanaudière"), in which case we take the first.
    """
    r = " ".join(raw.replace("\xa0", " ").split())
    r = r.replace(" (Eastern Townships)", "")
    joined = re.match(r"^(.*?[a-zé])and\s+[A-ZÉ]", r)
    if joined:
        r = joined.group(1)
    return r


# ── Name normalisation ────────────────────────────────────────────────────────

def normalise_name(s: str) -> str:
    """
    Lowercase, strip accents, fold every dash and apostrophe variant.

    Same idea as build_elasticity.normalise_name, extended for Quebec: division
    names carry curly apostrophes (L’Érable) and en-dashes (Anjou–Louis-Riel)
    that differ between sources for the same division.
    """
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    for ch in "-\u2013\u2014\x96\x97/":
        s = s.replace(ch, " ")
    for ch in "'’.":
        s = s.replace(ch, "")
    return " ".join(s.split())


# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch(url: str, filename: str) -> bytes:
    """Fetch a URL, caching to .cache/qc so repeated runs stay fast and polite."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / filename
    if cached.exists():
        print(f"  using cached {cached}")
        return cached.read_bytes()
    print(f"  fetching {url} …")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    cached.write_bytes(data)
    return data


# ── Source 1: 2022 results ────────────────────────────────────────────────────

def load_2022_results() -> dict[str, dict]:
    """{normalised 2022 name: {name, shares{party: pct}, valid_votes, electors}}"""
    data = json.loads(fetch(RESULTS_2022_URL, "resultats2022.json").decode("utf-8"))

    divisions = {}
    for circ in data["circonscriptions"]:
        name = circ["nomCirconscription"]
        valid = circ["nbVoteValide"]
        votes = dict.fromkeys(PARTIES, 0)
        for cand in circ["candidats"]:
            party = PARTY_MAP.get(cand["abreviationPartiPolitique"], "OTH")
            votes[party] += cand["nbVoteTotal"]
        divisions[normalise_name(name)] = {
            "name": name,
            # Share of valid votes, so each division sums to exactly 100.
            "shares": {p: votes[p] / valid * 100 for p in PARTIES},
            "valid_votes": valid,
            "electors": circ["nbElecteurInscrit"],
        }

    # Assert we reproduce the published province-wide result before going on.
    total = sum(d["valid_votes"] for d in divisions.values())
    prov = {
        p: sum(d["shares"][p] * d["valid_votes"] for d in divisions.values()) / total
        for p in PARTIES
    }
    expected = {"CAQ": 40.98, "QS": 15.43, "PQ": 14.61, "PLQ": 14.37, "PCQ": 12.91}
    for party, want in expected.items():
        if abs(prov[party] - want) > 0.02:
            sys.exit(
                f"ERROR: 2022 {party} share {prov[party]:.2f}% != published {want}%. "
                "Party mapping is wrong."
            )
    print(f"  2022 province-wide reproduces published result "
          f"({', '.join(f'{p} {prov[p]:.2f}%' for p in PARTIES)})")
    return divisions


# ── Source 2: 2026 divisions and francophone share ────────────────────────────

def load_2026_divisions() -> dict[str, str]:
    """{normalised 2026 name: official name} from the official division list."""
    # cp1252, not latin-1: these files use 0x96 for the en-dash in names like
    # "Anjou–Louis-Riel", which latin-1 would decode as a control character.
    raw = fetch(DIVISIONS_2026_URL, "divisions2026.csv").decode("cp1252")
    divisions = {}
    for row in csv.DictReader(raw.splitlines(), delimiter=";"):
        name = (row.get("CIRCONSCRIPTION 2026") or "").strip()
        if name:
            divisions[normalise_name(name)] = name
    return divisions


def load_franco_share() -> dict[str, float]:
    """
    {normalised 2026 name: francophone %} from the 2021 Census profiles.

    The sheet is transposed: divisions are columns, variables are rows. Row 285
    is "Français (pourcentage)" of mother tongue; row 1 (+ row 2 for names that
    wrap) carries the division names.
    """
    try:
        import xlrd
    except ImportError:
        sys.exit(
            "ERROR: xlrd is required to read the census .xls.\n"
            "       pip install -r requirements-build.txt"
        )

    path = CACHE_DIR / "census2021.xls"
    fetch(CENSUS_XLS_URL, "census2021.xls")
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_name("127 CEP 2026 ")

    label = str(sheet.cell_value(285, 1)).strip()
    if "Fran" not in label or "pourcentage" not in label:
        sys.exit(f"ERROR: census row 285 is {label!r}, not the French percentage row.")

    shares = {}
    for col in range(3, sheet.ncols):
        name = (str(sheet.cell_value(1, col)).strip()
                + str(sheet.cell_value(2, col)).strip()).strip()
        value = sheet.cell_value(285, col)
        if name and isinstance(value, float) and value:
            shares[normalise_name(name)] = value * 100
    return shares


# ── Source 3: region ──────────────────────────────────────────────────────────

def load_regions() -> dict[str, str]:
    """{normalised 2026 name: region} from Wikipedia's district list."""
    page = fetch(WIKI_DISTRICTS_URL, "districts.html").decode("utf-8", errors="replace")
    table = re.search(r"<table[^>]*wikitable.*?</table>", page, re.S)
    if not table:
        sys.exit("ERROR: no wikitable found in the Wikipedia district list.")

    regions = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        if len(cells) < 2:
            continue
        text = [html_mod.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells]
        if text[0].lower() == "district":
            continue
        regions[normalise_name(text[0])] = canonical_region(text[1])
    return regions


# ── Mapping 2022 -> 2026 ──────────────────────────────────────────────────────

def build_baseline() -> list[dict]:
    print("2022 results:")
    results_2022 = load_2022_results()
    print("2026 divisions:")
    divisions_2026 = load_2026_divisions()
    print("Census francophone share:")
    franco = load_franco_share()
    print("Regions:")
    regions = load_regions()

    print(f"\n  {len(results_2022)} divisions in 2022, {len(divisions_2026)} in 2026")

    # Key the manual tables by normalised name: the official 2026 list uses
    # curly apostrophes and en-dashes that the literals above don't reproduce.
    renames = {normalise_name(k): v for k, v in RENAMES.items()}
    new_divisions = {normalise_name(k): v for k, v in NEW_DIVISIONS.items()}
    for table, label in ((renames, "RENAMES"), (new_divisions, "NEW_DIVISIONS")):
        unknown = [n for n in table if n not in divisions_2026]
        if unknown:
            sys.exit(f"ERROR: {label} names not in the 2026 division list: {unknown}")

    rows = []
    counts = {"exact": 0, "rename": 0, "derived": 0}

    for code, (norm_2026, name_2026) in enumerate(sorted(divisions_2026.items()), start=1):
        # Which 2022 division(s) this one inherits from, and with what weight.
        if norm_2026 in new_divisions:
            donors, quality = new_divisions[norm_2026], "derived"
        elif norm_2026 in renames:
            donors, quality = [renames[norm_2026]], "rename"
        elif norm_2026 in results_2022:
            donors, quality = [results_2022[norm_2026]["name"]], "exact"
        else:
            sys.exit(
                f"ERROR: no 2022 predecessor for {name_2026!r}. Add it to RENAMES "
                "or NEW_DIVISIONS."
            )

        resolved = []
        for donor in donors:
            entry = results_2022.get(normalise_name(donor))
            if entry is None:
                sys.exit(f"ERROR: donor {donor!r} for {name_2026!r} not found in 2022 results.")
            resolved.append(entry)

        total_weight = sum(d["electors"] for d in resolved)
        shares = {
            p: sum(d["shares"][p] * d["electors"] for d in resolved) / total_weight
            for p in PARTIES
        }
        votes = sum(d["valid_votes"] for d in resolved) // len(resolved)
        winner = max(
            (p for p in PARTIES if p != "OTH"), key=lambda p: shares[p]
        )
        counts[quality] += 1

        row = {
            "riding_code": f"{code:03d}",
            "riding_name": name_2026,
            "province": "Quebec",
            "region": regions.get(norm_2026, "Unknown"),
            **{f"{p}_pct": round(shares[p], 2) for p in PARTIES},
            "winner": winner,
            "total_votes": votes,
            "franco_pct": round(
                FRANCO_SWING_OVERRIDES.get(name_2026, franco.get(norm_2026, 0.0)), 2
            ),
            "baseline_quality": quality,
        }
        rows.append(row)

    # ── Assertions: the defence against a silent mapping failure ──────────────
    if len(rows) != 127:
        sys.exit(f"ERROR: produced {len(rows)} divisions, expected 127.")

    missing_region = [r["riding_name"] for r in rows if r["region"] == "Unknown"]
    if missing_region:
        print(f"  WARNING: no region for {len(missing_region)}: {missing_region[:5]}",
              file=sys.stderr)

    unknown_overrides = set(FRANCO_SWING_OVERRIDES) - {r["riding_name"] for r in rows}
    if unknown_overrides:
        sys.exit(f"ERROR: FRANCO_SWING_OVERRIDES names not found: {unknown_overrides}")

    missing_franco = [r["riding_name"] for r in rows if not r["franco_pct"]]
    if missing_franco:
        sys.exit(f"ERROR: no francophone share for {missing_franco}")

    weighted_franco = (
        sum(r["franco_pct"] * r["total_votes"] for r in rows)
        / sum(r["total_votes"] for r in rows)
    )
    if not 74.0 <= weighted_franco <= 82.0:
        sys.exit(
            f"ERROR: vote-weighted francophone share {weighted_franco:.1f}% is outside "
            "the expected 74-82% band — wrong census column?"
        )

    print(f"\n  matched: {counts['exact']} exact, {counts['rename']} renamed, "
          f"{counts['derived']} derived")
    print(f"  vote-weighted francophone share: {weighted_franco:.1f}%")
    return rows


def main() -> None:
    rows = build_baseline()

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "riding_code", "riding_name", "province", "region",
        *[f"{p}_pct" for p in PARTIES],
        "winner", "total_votes", "franco_pct", "baseline_quality",
    ]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved → {OUTPUT_CSV}  ({len(rows)} divisions)")

    seats = {}
    for r in rows:
        seats[r["winner"]] = seats.get(r["winner"], 0) + 1
    print("  notional 2022 seats on the new map:",
          ", ".join(f"{p} {n}" for p, n in sorted(seats.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
