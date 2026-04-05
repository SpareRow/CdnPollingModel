#!/usr/bin/env python3
"""
Scrapes federal vote intention data from:
  - Liaison Strategies  (numbers in HTML post body)
  - EKOS Politics       (data-table PDFs linked from posts)
  - Leger               (voting-intention PDFs linked from posts)

Usage:
  python3 pdf_poll_scraper.py              # run all three
  python3 pdf_poll_scraper.py liaison      # run one firm
  python3 pdf_poll_scraper.py ekos leger   # run two firms

Requirements:
  pip install requests beautifulsoup4 pdfplumber
"""

import argparse
import csv
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:
    print("pdfplumber not installed. Run: pip install pdfplumber")
    sys.exit(1)

DATA_DIR = Path(__file__).parent
CSV_FIELDS = ["Date", "Liberal", "CPC", "NDP", "BQ", "Green"]
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (compatible; poll-tracker/1.0)"


def get(url, **kwargs):
    time.sleep(0.5)  # be polite
    r = SESSION.get(url, timeout=30, **kwargs)
    r.raise_for_status()
    return r


def save_csv(rows, path):
    rows = sorted(rows, key=lambda r: r.get("Date", ""))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows)} rows → {path}")


# ──────────────────────────────────────────────────────────────────────────────
# LIAISON STRATEGIES
# Numbers live in free-form paragraph text; date in JSON-LD.
# ──────────────────────────────────────────────────────────────────────────────

LIAISON_BASE = "https://press.liaisonstrategies.ca"

# Each tuple: (column name, list of regex patterns to try in order)
LIAISON_PARTY_RES = [
    ("Liberal", [
        re.compile(r'[Ll]iberal[s]?\s+(?:would\s+secure\s+|at\s+|up\s+to\s+)?(\d+)%'),
        re.compile(r'(\d+)%.*?[Ll]iberal'),
    ]),
    ("CPC", [
        re.compile(r'[Cc]onservative\s+[Pp]arty.*?(\d+)%'),
        re.compile(r'[Cc]onservative[s]?\s+(?:at\s+|stands?\s+at\s+)?(\d+)%'),
        re.compile(r'(\d+)%.*?[Cc]onservative'),
    ]),
    ("NDP", [
        re.compile(r'(?:NDP|New Democratic\s+Party\s+\(NDP\))\s+(?:holding\s+|at\s+)?(\d+)%'),
        re.compile(r'(\d+)%.*?NDP'),
    ]),
    ("BQ", [
        re.compile(r'[Bb]loc\s+[Qq]u[eé]b[eé]cois\s+(?:at\s+)?(\d+)%'),
        re.compile(r'(\d+)%.*?[Bb]loc'),
    ]),
    ("Green", [
        re.compile(r'[Gg]reen\s+[Pp]arty\s+(?:sits?\s+at\s+|at\s+)?(\d+)%'),
        re.compile(r'(\d+)%.*?[Gg]reen\s+[Pp]arty'),
    ]),
]


def parse_liaison_post(url):
    r = get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Date from JSON-LD
    date = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and "datePublished" in data:
                date = data["datePublished"][:10]
                break
        except Exception:
            pass

    if not date:
        return None

    # Body text
    article = soup.find("article") or soup.find("div", class_=re.compile(r"post-content|entry-content|gh-content"))
    text = article.get_text(" ") if article else soup.get_text(" ")

    result = {"Date": date}
    for party, patterns in LIAISON_PARTY_RES:
        for pat in patterns:
            m = pat.search(text)
            if m:
                result[party] = f"{m.group(1)}%"
                break

    if result.get("Liberal") and result.get("CPC"):
        return result
    return None


def scrape_liaison():
    print("\n=== Liaison Strategies ===")
    seen_urls = set()
    post_urls = []
    page = 1

    while page <= 30:
        url = LIAISON_BASE if page == 1 else f"{LIAISON_BASE}/page/{page}/"
        try:
            r = get(url)
        except requests.HTTPError:
            break
        soup = BeautifulSoup(r.text, "html.parser")

        new = 0
        for a in soup.find_all("a", href=True):
            title = a.get_text().strip()
            href = a["href"]
            if href.startswith(LIAISON_BASE) and href not in seen_urls:
                if re.search(r'(Federal Tracker|National Tracker)', title):
                    seen_urls.add(href)
                    post_urls.append(href)
                    new += 1

        print(f"  Page {page}: +{new} tracker posts (total {len(post_urls)})")

        # Ghost paginates with "Older posts" / next link
        next_link = soup.find("a", string=re.compile(r'[Oo]lder|[Nn]ext'))
        if not next_link:
            break
        page += 1

    rows = []
    for url in post_urls:
        row = parse_liaison_post(url)
        if row:
            rows.append(row)
            print(f"  ✓ {row['Date']}: LPC={row.get('Liberal')} CPC={row.get('CPC')} NDP={row.get('NDP')}")
        else:
            slug = url.rstrip("/").split("/")[-1]
            print(f"  ✗ parse failed: {slug}")

    save_csv(rows, DATA_DIR / "liaison_vote_intention.csv")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# EKOS POLITICS
# Each post links to a "datatables" PDF; parse the PDF for party percentages.
# ──────────────────────────────────────────────────────────────────────────────

EKOS_CATEGORY = "https://www.ekospolitics.com/index.php/category/national-vote-intention/"
EKOS_PARTY_LABELS = [
    ("Liberal",  ["liberal"]),
    ("CPC",      ["conservative"]),
    ("NDP",      ["ndp", "new democrat"]),
    ("BQ",       ["bloc"]),
    ("Green",    ["green"]),
]


def find_ekos_datatables_pdf(post_url):
    r = get(post_url)
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "datatables" in href.lower() and href.lower().endswith(".pdf"):
            return href
    return None


def parse_poll_pdf(pdf_bytes):
    """
    Generic PDF parser: tries table extraction then text extraction.
    Returns dict of {party: "XX%"} or None.
    """
    parties = {}
    label_map = {label: col for col, labels in EKOS_PARTY_LABELS for label in labels}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            # ── Table extraction ──────────────────────────────────────────
            for table in (page.extract_tables() or []):
                for row in table:
                    if not row:
                        continue
                    cells = [str(c).strip() if c else "" for c in row]
                    row_lower = " ".join(cells).lower()
                    matched_col = next((col for lbl, col in label_map.items() if lbl in row_lower), None)
                    if matched_col:
                        # Look for a percentage in the row
                        pct = next((re.search(r'(\d+\.?\d*)\s*%', c) for c in cells if c), None)
                        if not pct:
                            # Sometimes just a plain number — take first numeric cell after the label
                            nums = [c for c in cells[1:] if re.match(r'\d+\.?\d*$', c)]
                            if nums:
                                parties[matched_col] = f"{nums[0]}%"
                        else:
                            parties[matched_col] = f"{float(pct.group(1)):.1f}%"

            # ── Text extraction fallback ──────────────────────────────────
            if len(parties) < 2:
                text = page.extract_text() or ""
                for col, labels in EKOS_PARTY_LABELS:
                    if col in parties:
                        continue
                    for lbl in labels:
                        m = re.search(rf'{lbl}[^\n]{{0,80}}?(\d+\.?\d*)\s*%', text, re.IGNORECASE)
                        if m:
                            parties[col] = f"{float(m.group(1)):.1f}%"
                            break

            if len(parties) >= 3:
                break

    return parties if len(parties) >= 2 else None


def get_ekos_post_date(post_url, soup):
    t = soup.find("time")
    if t and t.get("datetime"):
        return t["datetime"][:10]
    m = re.search(r'/(\d{4})/(\d{2})/', post_url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    return None


def scrape_ekos():
    print("\n=== EKOS Politics ===")
    r = get(EKOS_CATEGORY)
    soup = BeautifulSoup(r.text, "html.parser")

    post_urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r'ekospolitics\.com/index\.php/20\d\d/', href) and href not in seen:
            seen.add(href)
            post_urls.append(href)

    print(f"  Found {len(post_urls)} posts")

    rows = []
    for post_url in post_urls:
        try:
            post_r = get(post_url)
            post_soup = BeautifulSoup(post_r.text, "html.parser")
            date = get_ekos_post_date(post_url, post_soup)
            pdf_url = find_ekos_datatables_pdf(post_url)
        except Exception as e:
            print(f"  ✗ fetch error: {post_url} ({e})")
            continue

        if not pdf_url:
            print(f"  ✗ no datatables PDF: {post_url.split('/')[-2]}")
            continue

        try:
            pdf_r = get(pdf_url)
            parties = parse_poll_pdf(pdf_r.content)
        except Exception as e:
            print(f"  ✗ PDF error: {pdf_url.split('/')[-1]} ({e})")
            continue

        if parties and date:
            row = {"Date": date, **{k: parties.get(k) for k in ["Liberal", "CPC", "NDP", "BQ", "Green"]}}
            rows.append(row)
            print(f"  ✓ {date}: LPC={parties.get('Liberal')} CPC={parties.get('CPC')} NDP={parties.get('NDP')}")
        else:
            print(f"  ✗ parse failed: {pdf_url.split('/')[-1]}")

    save_csv(rows, DATA_DIR / "ekos_vote_intention.csv")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# LEGER
# Blog posts link to voting-intention PDFs in wp-content/uploads.
# ──────────────────────────────────────────────────────────────────────────────

LEGER_LISTING_URLS = [
    "https://leger360.com/in-the-news/",
]

LEGER_PDF_RE = re.compile(
    r'https://leger360\.com/wp-content/uploads/\d{4}/\d{2}/[^"\'<>\s]+\.pdf',
    re.IGNORECASE,
)

LEGER_PARTY_LABELS = [
    ("Liberal",  ["liberal"]),
    ("CPC",      ["conservative"]),
    ("NDP",      ["ndp", "new democrat"]),
    ("BQ",       ["bloc"]),
    ("Green",    ["green"]),
]


def is_leger_vote_pdf(url):
    name = url.split("/")[-1].lower()
    return any(kw in name for kw in ["vote", "voting", "intention", "intentions", "electi"])


def date_from_leger_url(url):
    m = re.search(r'/(\d{4})/(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    return None


def scrape_leger():
    print("\n=== Leger ===")
    pdf_urls = set()

    for base_url in LEGER_LISTING_URLS:
        for page in range(1, 20):
            url = f"{base_url}page/{page}/" if page > 1 else base_url
            try:
                r = get(url)
            except requests.HTTPError:
                break

            found = [u for u in LEGER_PDF_RE.findall(r.text) if is_leger_vote_pdf(u)]
            pdf_urls.update(found)

            # Also follow post links to find PDFs embedded inside posts
            soup = BeautifulSoup(r.text, "html.parser")
            post_links = [
                a["href"] for a in soup.find_all("a", href=True)
                if re.search(r'leger360\.com/(in-the-news|fed-pol)', a["href"])
                and re.search(r'(vote|voting|intention|federal)', a.get_text() + a["href"], re.IGNORECASE)
            ]

            for post_url in post_links[:10]:  # cap to avoid too many requests
                try:
                    pr = get(post_url)
                    pdfs = [u for u in LEGER_PDF_RE.findall(pr.text) if is_leger_vote_pdf(u)]
                    pdf_urls.update(pdfs)
                except Exception:
                    pass

            print(f"  Page {page}: {len(pdf_urls)} PDFs found so far")

            next_link = soup.find("a", string=re.compile(r'[Oo]lder|[Nn]ext'))
            if not next_link:
                break

    print(f"  Total unique PDFs: {len(pdf_urls)}")

    rows = []
    for pdf_url in sorted(pdf_urls):
        date = date_from_leger_url(pdf_url)
        try:
            r = get(pdf_url)
            parties = parse_poll_pdf(r.content)
        except Exception as e:
            print(f"  ✗ {pdf_url.split('/')[-1]}: {e}")
            continue

        if parties and date:
            row = {"Date": date, **{k: parties.get(k) for k in ["Liberal", "CPC", "NDP", "BQ", "Green"]}}
            rows.append(row)
            print(f"  ✓ {date}: LPC={parties.get('Liberal')} CPC={parties.get('CPC')}")
        else:
            print(f"  ✗ parse failed: {pdf_url.split('/')[-1]}")

    save_csv(rows, DATA_DIR / "leger_vote_intention.csv")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Canadian federal vote intention polls")
    parser.add_argument(
        "firms",
        nargs="*",
        choices=["liaison", "ekos", "leger"],
        help="Firms to scrape (default: all)",
    )
    args = parser.parse_args()
    firms = args.firms or ["liaison", "ekos", "leger"]

    if "liaison" in firms:
        scrape_liaison()
    if "ekos" in firms:
        scrape_ekos()
    if "leger" in firms:
        scrape_leger()
