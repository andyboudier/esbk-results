"""Scrape the RFME ESBK page into a PDF inventory with metadata.

Walks the results page structure:
  <h*>Resultados YYYY</h*>
    <h*>N. Circuit Name - dates</h*>
      <table> columns = class, rows = session, cells = PDF links

Outputs data/inventory.json: one record per result PDF with
year / event / circuit / event_dates / class / session / url.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://rfme.com/campeonatos/campeonato-de-espana-de-superbike/"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "inventory.json"

SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s


def parse_event_heading(text: str):
    """'1. Circuito de Jerez Ángel Nieto – 20, 21 y 22 de marzo de 2026'
    -> (round 1, circuit name, date range)."""
    text = norm(text).rstrip(":")
    m = re.match(r"^(\d+)\s*\.\s*(.+)$", text)
    rnd = None
    rest = text
    if m:
        rnd = int(m.group(1))
        rest = m.group(2)
    # Split circuit from date part on the first dash surrounded by spaces
    parts = re.split(r"\s+[–—-]\s+", rest, maxsplit=1)
    circuit = norm(parts[0])
    dates_text = norm(parts[1]) if len(parts) > 1 else ""
    start, end = parse_date_range(dates_text)
    return rnd, circuit, dates_text, start, end


def parse_date_range(text: str):
    """Extract first/last ISO dates from Spanish date text like
    '20, 21 y 22 de marzo de 2026' or '30 y 31 de agosto y 1 de septiembre de 2024'."""
    if not text:
        return None, None
    year_m = re.search(r"(20\d\d)", text)
    if not year_m:
        return None, None
    year = int(year_m.group(1))
    # Find day-list + month groups: e.g. '20, 21 y 22 de marzo'
    dates = []
    for m in re.finditer(r"((?:\d{1,2}[\s,y]+)*\d{1,2})\s+de\s+([a-záéíóúñ]+)", text.lower()):
        month = SPANISH_MONTHS.get(m.group(2))
        if not month:
            continue
        days = [int(d) for d in re.findall(r"\d{1,2}", m.group(1))]
        for d in days:
            if 1 <= d <= 31:
                dates.append(f"{year:04d}-{month:02d}-{d:02d}")
    if not dates:
        return None, None
    dates.sort()
    return dates[0], dates[-1]


def clean_class(text: str) -> str:
    return norm(text)


def scrape(html: str):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    current_year = None
    current_event = None

    headings = re.compile(r"^h[1-6]$")
    for el in soup.find_all([*(f"h{i}" for i in range(1, 7)), "figure", "table", "ul"]):
        if headings.match(el.name):
            text = norm(el.get_text())
            ym = re.match(r"^Resultados\s+(20\d\d)$", text, re.I)
            if ym:
                current_year = int(ym.group(1))
                current_event = None
                continue
            if current_year and re.match(r"^\d+\s*\.", text) or (
                current_year and text.lower().startswith("test")
            ):
                rnd, circuit, dates_text, start, end = parse_event_heading(text)
                current_event = {
                    "year": current_year,
                    "round": rnd,
                    "circuit": circuit,
                    "heading": text,
                    "dates_text": dates_text,
                    "date_start": start,
                    "date_end": end,
                }
                continue
            # Any other heading outside a results year ends the event context
            if not re.match(r"^\d+\s*\.", text) and current_year is None:
                current_event = None
        elif el.name == "ul" and current_event and current_year:
            # 2021-style icon lists: anchor text is "CLASS - SESSION"
            if "kt-svg-icon-list" not in " ".join(el.get("class", [])):
                continue
            for a in el.find_all("a", href=True):
                href = a["href"]
                if ".pdf" not in href.lower():
                    continue
                if href.startswith("/"):
                    href = "https://rfme.com" + href
                text = norm(a.get_text())
                parts = re.split(r"\s+[–—-]\s+", text, maxsplit=1)
                if len(parts) == 2:
                    klass, session = parts[0], parts[1]
                else:
                    klass, session = "", text
                records.append({
                    **current_event,
                    "class": norm(klass),
                    "session": norm(session),
                    "url": href,
                })
        elif el.name == "table" and current_event and current_year:
            rows = el.find_all("tr")
            if not rows:
                continue
            # Header row: class names
            header_cells = rows[0].find_all(["th", "td"])
            classes = [clean_class(c.get_text()) for c in header_cells]
            for row in rows[1:]:
                cells = row.find_all(["th", "td"])
                for idx, cell in enumerate(cells):
                    for a in cell.find_all("a", href=True):
                        href = a["href"]
                        if ".pdf" not in href.lower():
                            continue
                        if href.startswith("/"):
                            href = "https://rfme.com" + href
                        session = norm(a.get_text())
                        klass = classes[idx] if idx < len(classes) else ""
                        records.append({
                            **current_event,
                            "class": klass,
                            "session": session,
                            "url": href,
                        })
    return records


CLASSIFICATION_KEYWORDS = re.compile(
    r"parrilla|grid|horario|inscrito|inscritos|boxes|informacion|información|"
    r"clasificaciones\s+(provisionales|finales)|comunicado",
    re.I,
)

RESULT_SESSION = re.compile(
    r"carrera|ent\.?\s*(libre|crono)|entrenamiento|warm|superpole|q1|q2|sesi[oó]n",
    re.I,
)


def classify(rec):
    """Tag whether a PDF is a timed-session result (worth parsing) or auxiliary."""
    s = rec["session"].lower()
    if re.search(r"parrilla|grid|salida", s):
        return "grid"
    if RESULT_SESSION.search(s):
        return "result"
    return "other"


def main():
    if len(sys.argv) > 1:
        html = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        html = requests.get(PAGE_URL, timeout=60, headers={
            "User-Agent": "Mozilla/5.0 (esbk-results ingest)"
        }).text
    records = scrape(html)
    for r in records:
        r["kind"] = classify(r)
        r["event_slug"] = f"{r['year']}-{r['round'] or 0:02d}-{slugify(r['circuit'])}"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1))
    kinds = {}
    for r in records:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"{len(records)} PDFs -> {OUT}")
    print("by kind:", kinds)
    years = {}
    for r in records:
        years[r["year"]] = years.get(r["year"], 0) + 1
    print("by year:", dict(sorted(years.items())))


if __name__ == "__main__":
    main()
