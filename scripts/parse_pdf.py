"""Parse ESBK result PDFs into a normalized session record.

Layouts handled (timing providers differ per circuit):
  cronojerez   - Jerez events (live.crono-jerez.com)
  booklet      - Estoril / MotorLand / Barcelona / Navarra booklets
                 (some encode text into the Unicode private-use area)
  mastertiming - Circuit Ricardo Tormo (MasterTiming)
  navarra      - Navarra 2026-style "ANÁLISIS VUELTA A VUELTA CON SECTORES"

Output schema (dict):
  layout, date, results[], laps[], top_speeds[], warnings[]
  results:    {pos,no,rider,nat,team,bike,laps,time,time_s,gap,best_lap,
               best_lap_s,best_lap_no,top_speed,tyres,status}
  laps:       {no, laps: [{lap,time,time_s,sectors:[s1..s4],speed}]}
  top_speeds: {no, rider, vmax, lap}
"""
import re
import unicodedata

import pdfplumber

BRANDS = [
    "KAWASAKI", "YAMAHA", "HONDA", "DUCATI", "APRILIA", "BMW", "KTM",
    "TRIUMPH", "SUZUKI", "MV AGUSTA", "BEON", "CORSARO", "HUSQVARNA",
]
BRAND_RE = "|".join(BRANDS)
TYRES = ["Dunlop", "Pirelli", "Michelin", "Bridgestone", "Continental", "Metzeler"]
NATS = set("""ESP POR PRT FRA ITA GBR GER DEU AUT SUI CHE BEL NED NLD AND ARG
AUS BRA BUL BGR CHI CHL CHN COL CRI CZE DEN DNK EST FIN GRE GRC HUN IRL ISR
JPN KOR LAT LVA LIT LTU LUX MAS MYS MEX MAR NOR NZL PER POL ROU RSA ZAF RUS
SMR SRB SVK SLO SVN SWE THA TUR UKR USA URU VEN IND IDN PHI SGP HKG TPE QAT
UAE SAU KUW MCO MON GUA DOM PAN ECU BOL PAR PRY""".split())

SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def decode_pua(t: str) -> str:
    return "".join(chr(ord(c) - 0xF000) if 0xF000 <= ord(c) <= 0xF0FF else c for c in t)


def t2s(tok):
    """'1:55.066' / '01:35,274' / '35.123' -> seconds."""
    if not tok:
        return None
    tok = tok.strip()
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})$", tok)
    if m:
        h, mnt, s, ms = m.groups()
        return round(int(h or 0) * 3600 + int(mnt) * 60 + int(s)
                     + int(ms.ljust(3, "0")) / 1000.0, 3)
    m = re.match(r"^(?:(\d+):)?(\d{1,2})[.,](\d{1,3})$", tok)
    if not m:
        return None
    mnt, s, ms = m.groups()
    return round(int(mnt or 0) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0, 3)


def fmt(tok):
    return tok.replace(",", ".") if tok else None


def fnum(tok):
    try:
        return float(tok.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def result_row(**kw):
    base = dict(pos=None, no=None, rider=None, nat=None, team=None, bike=None,
                laps=None, time=None, time_s=None, gap=None, best_lap=None,
                best_lap_s=None, best_lap_no=None, top_speed=None, tyres=None,
                status="OK")
    base.update(kw)
    return base


def find_date(text: str):
    m = re.search(r"(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚÑa-záéíóúñ]+)\s+DE\s+(20\d\d)",
                  text, re.I)
    if m:
        month = SPANISH_MONTHS.get(m.group(2).lower())
        if month:
            return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(20\d\d)", text, re.I)
    if m:
        month = SPANISH_MONTHS.get(m.group(2).lower())
        if month:
            return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{2})/(\d{2})/(20\d\d)\b", text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def detect_layout(full_text: str):
    if "crono-jerez" in full_text or "CLASIFICACIÓN DE CARRERA" in full_text \
            or "RESULTADOS ENTRENAMIENTOS" in full_text:
        return "cronojerez"
    if "ANÁLISIS VUELTA A VU" in full_text or "Nº Corredor" in full_text \
            or "MEJORES VELOCIDADES" in full_text and "T.Transc" in full_text:
        return "navarra"
    if "MasterTiming" in full_text or "ANALYSIS / SECTORS" in full_text:
        return "mastertiming"
    if re.search(r"Vuelta a vuelta|Best Top Speed|T\. Sesión|Lap by lap|"
                 r"Starting Grid|Parrilla de salida|Best Lap|Mejor Vuelta|"
                 r"^Clasificaci..?n\s*$|^Classification\s*$", full_text, re.M):
        return "booklet"
    return None


def split_rider_nat_team(blob):
    """'STEVEN ODENDAAL RSA NEW2 PROJECT' -> (name, nat, team).
    Handles the NAT being concatenated to the name ('...PALOMERESP')."""
    for m in re.finditer(r"([A-Z]{2,3})(?=\s|$)", blob):
        if m.group(1) in NATS and blob[:m.start()].strip().count(" ") >= 0 \
                and m.start() > 3:
            name = blob[:m.start()].strip()
            team = blob[m.end():].strip() or None
            if name:
                return name, m.group(1), team
    return blob.strip(), None, None


def split_name_team(blob, name_map, no):
    """'Marc VICH BOX 77' + known name 'Marc VICH' -> ('Marc VICH', 'BOX 77').
    Falls back to a caps heuristic."""
    blob = blob.strip()
    known = name_map.get(no)

    def simplify(s):
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return re.sub(r"[^A-Z0-9]", "", s.upper())

    if known:
        sb, sk = simplify(blob), simplify(known)
        if sb.startswith(sk):
            consumed, cut = 0, len(blob)
            for i, ch in enumerate(blob):
                consumed += len(simplify(ch))
                if consumed >= len(sk):
                    cut = i + 1
                    break
            return blob[:cut].strip(), blob[cut:].strip() or None
    # heuristic: given names are Mixed Case, surname = following caps tokens
    toks = blob.split()
    surname_join = {"DE", "LA", "DEL", "DA", "DI", "DOS", "VAN", "DER", "EL", "MC"}
    i = 0
    while i < len(toks) and not toks[i].isupper():
        i += 1  # given names
    if i == 0 or i >= len(toks):  # all caps or no surname: cannot split
        return blob, None
    j = i
    while j < len(toks) and toks[j].isupper():
        j += 1
        if toks[j - 1] not in surname_join:
            break
    return " ".join(toks[:j]), " ".join(toks[j:]) or None


# --------------------------------------------------------------------------
# shared lap helpers
# --------------------------------------------------------------------------

def _get_lap(out, no, lap):
    lr = next((l for l in out["laps"] if l["no"] == no), None)
    if lr is None:
        lr = dict(no=no, laps=[])
        out["laps"].append(lr)
    l = next((x for x in lr["laps"] if x["lap"] == lap), None)
    if l is None:
        l = dict(lap=lap, time=None, time_s=None, sectors=None, speed=None)
        lr["laps"].append(l)
    return l


def _add_lap(out, no, lap, time=None, time_s=None, sectors=None, speed=None):
    l = _get_lap(out, no, lap)
    if time and not l["time"]:
        l["time"], l["time_s"] = fmt(time), time_s
    if sectors and not l["sectors"]:
        l["sectors"] = [round(s, 3) if s is not None else None for s in sectors]
    if speed and not l["speed"]:
        l["speed"] = speed


def _add_lap_sector(out, no, lap, idx, val, size=4):
    l = _get_lap(out, no, lap)
    if l["sectors"] is None:
        l["sectors"] = [None] * size
    while len(l["sectors"]) <= idx:
        l["sectors"].append(None)
    if l["sectors"][idx] is None:
        l["sectors"][idx] = round(val, 3)


def _set_vmax(out, no, rider, vmax, lap):
    cur = next((t for t in out["top_speeds"] if t["no"] == no), None)
    if cur is None:
        out["top_speeds"].append(dict(no=no, rider=rider, vmax=vmax, lap=lap))
    elif vmax > cur["vmax"]:
        cur.update(vmax=vmax, lap=lap)
        if rider:
            cur["rider"] = rider
    for r in out["results"]:
        if r["no"] == no and (r["top_speed"] is None or vmax > r["top_speed"]):
            r["top_speed"] = vmax


def _halves_text(page):
    """Reconstruct left/right column text from word coordinates (cropping
    truncates characters at the split; word-based splitting keeps them)."""
    words = page.extract_words()
    mid = page.width / 2
    for side in (0, 1):
        sel = [w for w in words
               if (side == 0) == ((w["x0"] + w["x1"]) / 2 < mid)]
        lines = {}
        for w in sel:
            lines.setdefault(round(w["top"] / 2.5), []).append(w)
        text = []
        for _, ws in sorted(lines.items()):
            text.append(" ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"])))
        yield decode_pua("\n".join(text))


# --------------------------------------------------------------------------
# cronojerez
# --------------------------------------------------------------------------

def parse_cronojerez(pdf, pages_text, out):
    # names from Vmax / practice pages help split "Name CLUB" blobs later
    name_map = {}
    for t in pages_text:
        if "MEJORES VELOCIDADES" in t or "TOP FIVE" in t:
            for line in t.splitlines():
                m = re.match(r"^(\d{1,3})\s+(.+?)\s+([A-Z]{3})\s", line.strip())
                if m and m.group(3) in NATS:
                    name_map.setdefault(int(m.group(1)), m.group(2).strip())

    race_pages = [i for i, t in enumerate(pages_text)
                  if re.search(r"^CLASIFICACIÓN( DE CARRERA)?\s*$", t, re.M)]
    practice_pages = [i for i, t in enumerate(pages_text)
                      if "RESULTADOS ENTRENAMIENTOS" in t]
    if race_pages:
        _cj_race_classification(pages_text[race_pages[0]], out, name_map)
    elif practice_pages:
        _cj_practice_classification(pages_text[practice_pages[0]], out)

    for i, t in enumerate(pages_text):
        if "VUELTA A VUELTA" in t:
            _cj_vuelta_a_vuelta(pdf.pages[i], out)
    for i, t in enumerate(pages_text):
        if re.search(r"\bANÁLISIS\b|\bANALISIS\b", t):
            _cj_analisis(pdf.pages[i], out)
    for t in pages_text:
        if "MEJORES VELOCIDADES" in t:
            _cj_vmax(t, out)
            break
    # practice classification carries VMax directly
    for r in out["results"]:
        if r["top_speed"]:
            _set_vmax(out, r["no"], r["rider"], r["top_speed"], None)


def _find_no(blob, name_map):
    """Reverse lookup: rider number whose known name prefixes the blob."""
    def simplify(s):
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return re.sub(r"[^A-Z0-9]", "", s.upper())

    sb = simplify(blob)
    best_no, best_len = None, 0
    for no, name in name_map.items():
        sk = simplify(name)
        if sk and sb.startswith(sk) and len(sk) > best_len:
            best_no, best_len = no, len(sk)
    return best_no


def _cj_race_classification(text, out, name_map):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    in_nc = False
    for line in lines:
        if re.match(r"^No Clasificad", line, re.I):
            in_nc = True
            continue
        # DSQ: '8 k Tobias R PEDERSEN.- DESCALIFICADO ...'
        m = re.match(r"^(?:(\d{1,2})\s+)?(\d{1,3})\s+(?:k\s+)?(.+?)\.?-+\s*"
                     r"(DESCALIFICAD|EXCLUID|NO SALIDA).*$", line)
        if m:
            pos, no, blob = m.group(1), int(m.group(2)), m.group(3)
            rider, team = split_name_team(blob, name_map, no)
            out["results"].append(result_row(
                pos=int(pos) if pos else None, no=no, rider=rider, team=team,
                status="DSQ"))
            continue
        if not in_nc:
            # '1 99 k Marc VICH BOX 77 KAWASAKI Dunlop 12 23:15.609 0.028 6 1:55.066'
            # 2021: '1 81 sbk Jordi TORRES TEAM HONDA LAGLISSE HONDA DUNLOP 16 27:22.212 ---- 6 1:42.027'
            # degenerate: 'Ivo LOPES BMW EASYRACE TEAM Dunlop 16 27:08.480 0.018 6 1:41.242'
            m = re.match(
                r"^(?:(\d{1,2})\s+)?(?:(\d{1,3})\s+)?(?:[a-z]\S{0,4}\s+)?([^\d].*?)\s+"
                r"(?:(" + BRAND_RE + r")\s*(?:MOT)?\s*)?"
                r"(?:(" + "|".join(TYRES) + r")\s+)?"
                r"(\d{1,2})\s+(\d+:\d{2}\.\d{3})\s+"
                r"(?:(----|\d[\d:]*\.\d{3}|\d+\s+Vueltas?)\s+)?"
                r"(\d{1,2})\s+(\d:\d{2}\.\d{3})\s*$", line, re.I)
            if m:
                (pos, no, blob, bike, tyre, nv, total, dif, vr, best) = m.groups()
                if pos is None:
                    prev = out["results"][-1]["pos"] if out["results"] else 0
                    pos = (prev or 0) + 1
                no = int(no) if no is not None else (_find_no(blob, name_map) or 0)
                blob = re.sub(r"^k\s+", "", blob)
                rider, team = split_name_team(blob, name_map, no)
                out["results"].append(result_row(
                    pos=int(pos), no=no, rider=rider, team=team,
                    bike=bike.upper() if bike else None,
                    tyres=tyre.capitalize() if tyre else None,
                    laps=int(nv), time=total, time_s=t2s(total),
                    gap=None if dif == "----" else dif,
                    best_lap=best, best_lap_s=t2s(best), best_lap_no=int(vr)))
                continue
        else:
            # '96 Alberto BELTRAN FLP RACING TEAM ESP 11 21:33.487 7 1:56.437'
            m = re.match(
                r"^(\d{1,3})\s+(?:k\s+)?(.+?)\s+([A-Z]{3})\s+"
                r"(\d{1,2})\s+(\d+:\d{2}\.\d{3})\s+(\d{1,2})\s+(\d:\d{2}\.\d{3})\s*$",
                line)
            if m:
                no, blob, nat, nv, total, vr, best = m.groups()
                no = int(no)
                blob = re.sub(r"\s+(" + BRAND_RE + r")[A-Z]*$", "", blob)
                rider, team = split_name_team(blob, name_map, no)
                bike = next((b for b in BRANDS if b[:7] in line.upper()), None)
                out["results"].append(result_row(
                    no=no, rider=rider, team=team, nat=nat if nat in NATS else None,
                    bike=bike, laps=int(nv), time=total, time_s=t2s(total),
                    best_lap=best, best_lap_s=t2s(best), best_lap_no=int(vr),
                    status="DNF"))
    if not out.get("date"):
        out["date"] = find_date(text)


def _cj_practice_classification(text, out):
    # '1 26 spb José Manuel OSUNA ESPDEZA-BOX 77 RACING TEA KAWASAKI 20 20 1:49.904 ---- ---- 216,7'
    tail = re.compile(
        r"\s+(\d{1,2})\s+(\d{1,2})\s+(\d:\d{2}\.\d{3})\s+(----|\S+)\s+"
        r"(----|\S+)\s+(\d{2,3},\d)\s*$")
    for line in text.splitlines():
        line = line.strip()
        tm = tail.search(line)
        hm = re.match(r"^(\d{1,2})\s+(\d{1,3})\s+(\S{1,4})\s+(.+)$",
                      line[:tm.start()] if tm else "")
        if not tm or not hm:
            continue
        nv, vr, best, gap, _intv, vmax = tm.groups()
        pos, no, _cls, blob = hm.groups()
        # blob = 'Name Surname NAT<TEAM...> [BRAND]'
        nat = team = bike = None
        bm = re.search(r"\s(" + BRAND_RE + r")\s*$", blob)
        if bm:
            bike = bm.group(1)
            blob = blob[:bm.start()]
        name = blob
        for nm in re.finditer(r"\s([A-Z]{3})(?![a-zà-ú])", blob):
            cand = nm.group(1)
            before = blob[:nm.start()]
            if cand in NATS and re.search(r"[A-ZÀ-Ú]{2}", before):
                name = before.strip()
                nat = cand
                team = blob[nm.end():].strip() or None
                break
        out["results"].append(result_row(
            pos=int(pos), no=int(no), rider=name.strip(), nat=nat,
            team=team or None, bike=bike, laps=int(nv),
            gap=None if gap == "----" else gap, best_lap=best,
            best_lap_s=t2s(best), best_lap_no=int(vr), top_speed=fnum(vmax)))
    if not out.get("date"):
        out["date"] = find_date(text)


def _words_by_line(page, tol=2.5):
    lines = {}
    for w in page.extract_words():
        lines.setdefault(round(w["top"] / tol), []).append(w)
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]


def _nearest_col(x, cols, tol=45):
    best, bd = None, 1e9
    for no, cx in cols.items():
        d = abs(x - cx)
        if d < bd:
            best, bd = no, d
    return best if bd < tol else None


def _cj_vuelta_a_vuelta(page, out):
    cols = {}
    for ws in _words_by_line(page):
        texts = [w["text"] for w in ws]
        if texts[0] == "Vuelta" and len(ws) > 1 and all(
                re.match(r"^\d+$", t) for t in texts[1:]):
            cols = {int(w["text"]): (w["x0"] + w["x1"]) / 2 for w in ws[1:]}
            continue
        if not cols:
            continue
        lapno = None
        for w in ws:
            if re.match(r"^\d{1,2}$", w["text"]) and w["x0"] < 70:
                lapno = int(w["text"])
                break
        if lapno is None:
            continue
        for w in ws:
            tok = w["text"].rstrip("*")
            sec = t2s(tok)
            if sec is None or sec < 20 or ":" not in tok:
                continue
            rider = _nearest_col((w["x0"] + w["x1"]) / 2, cols)
            if rider is not None:
                _add_lap(out, rider, lapno, time=tok, time_s=sec)


LABELS = ("IP1", "IP2", "IP3", "IP4", "FL", "F", "FL5")


def _cj_analisis(page, out):
    """Sector analysis: rows grouped IP1..FL per lap; the big lap number is
    vertically centred over the group so it may land on any row."""
    cols = {}
    groups = []   # [{lapno, rows: {label: {rider: [(x, tok, sec), ..]}}}]
    cur = None

    def flush():
        nonlocal cur
        if cur and cur["rows"]:
            groups.append(cur)
        cur = None

    for ws in _words_by_line(page):
        texts = [w["text"] for w in ws]
        if texts[0] == "Número":
            cols = {int(w["text"]): (w["x0"] + w["x1"]) / 2
                    for w in ws[1:]
                    if re.match(r"^\d+$", w["text"]) and w["text"] != "0"}
            continue
        if not cols:
            continue
        label = next((w["text"] for w in ws if w["text"] in LABELS), None)
        lapno = next((int(w["text"]) for w in ws
                      if re.match(r"^\d{1,2}$", w["text"]) and w["x0"] < 60), None)
        if label is None and lapno is None:
            continue
        if label == "IP1" and cur and "IP1" in cur["rows"]:
            flush()
        if cur is None:
            cur = dict(lapno=None, rows={})
        if lapno is not None:
            cur["lapno"] = lapno
        if label is not None:
            vals = {}
            for w in ws:
                sec = t2s(w["text"].rstrip("*"))
                if sec is None:
                    continue
                rider = _nearest_col((w["x0"] + w["x1"]) / 2, cols)
                if rider is not None:
                    vals.setdefault(rider, []).append(
                        (w["x0"], w["text"].rstrip("*"), sec))
            cur["rows"][label] = vals
    flush()

    has_ip4 = any("IP4" in g["rows"] for g in groups)
    size = 5 if has_ip4 else 4
    fl_idx = size - 1
    prev_lap = 0
    for g in groups:
        lap = g["lapno"] if g["lapno"] is not None else prev_lap + 1
        prev_lap = lap
        for label, vals in g["rows"].items():
            idx = fl_idx if label in ("FL", "F", "FL5") else int(label[2]) - 1
            for rider, vv in vals.items():
                vv.sort()
                _add_lap_sector(out, rider, lap, idx, vv[0][2], size=size)
                if idx == fl_idx and len(vv) > 1:
                    _add_lap(out, rider, lap, time=vv[1][1], time_s=vv[1][2])


def _cj_vmax(text, out):
    for line in text.splitlines():
        m = re.match(r"^(\d{1,3})\s+(.+?)\s+([A-Z](?:\s?[A-Z]){2})\s+(.*?)\s+"
                     r"\d{1,2}:\d{2}:\d{2}\s+(\d{1,2})\s+(\d{2,3},\d)\s*$",
                     line.strip())
        if not m or m.group(3).replace(" ", "") not in NATS:
            continue
        no, name, _nat, _teambike, lap, v = m.groups()
        _set_vmax(out, int(no), name.strip(), fnum(v), int(lap))


# --------------------------------------------------------------------------
# booklet
# --------------------------------------------------------------------------

def parse_booklet(pdf, pages_text, out):
    for t in pages_text:
        head = "\n".join(t.splitlines()[:8])
        if re.search(r"^(Classification|Clasificaci..?n)\s*$", head, re.M):
            if "Starting Grid" in head or "Parrilla" in head:
                continue
            _booklet_classification(t, out)
            if not out["results"]:
                _booklet_practice_classification(t, out)
            break
    out["_cur_rider"] = None
    for i, t in enumerate(pages_text):
        head = "\n".join(t.splitlines()[:6])
        if re.search(r"Vuelta a vuelta|Lap by lap|Sector Analysis", head):
            for half_text in _halves_text(pdf.pages[i]):
                _booklet_laps(half_text, out)
    out.pop("_cur_rider", None)
    for t in pages_text:
        if re.search(r"Best Top Speed|velocidades máximas|Velociad|"
                     r"Velocidad máxima", t):
            _booklet_vmax(t, out)
    # backfill rider/team split from lap-page names
    names = {lr["no"]: lr["name"] for lr in out.get("_lap_riders", [])}
    for r in out["results"]:
        if r["no"] in names and r["rider"]:
            rider, team2 = split_name_team(r["rider"], names, r["no"])
            r["rider"] = rider
            if team2 and not r["team"]:
                r["team"] = team2
    out.pop("_lap_riders", None)


def _booklet_classification(text, out):
    tyre_re = "|".join(TYRES)
    nat_re = "|".join(sorted(NATS))
    # '2 4QABIL IRFAN MAS FRANDO RACING ... Pirelli Talent 11 22:59.531 +0.062145.7 11 2:04.301 147.0'
    pat = re.compile(
        r"^(\d{1,2})?\s*(\d{1,3})\s*([A-ZÀ-Ú].+?)\s+(" + nat_re + r")\s+(.*?)\s*"
        r"(?:(" + tyre_re + r")\s+)?(\S+)\s+(\d{1,2})\s+(\d+:\d{2}\.\d{3})"
        r"(?:\s+(-|\+\d[\d:]*(?:\.\d{1,3})?|\+?-?\d+\s*(?:Laps?|Vlts?\.?))\s*"
        r"(\d{2,3}\.\d))?\s+(\d{1,2})\s+(\d+:\d{2}\.\d{3})\s*(\d{2,3}\.\d)\s*$")
    for raw in text.splitlines():
        line = raw.strip()
        m = pat.match(line)
        if not m:
            continue
        (pos, no, name_team, nat, team2, tyre, _cls, laps, total, gap, _kph,
         blap_no, blap, _bkph) = m.groups()
        blob = (name_team.strip() + " " + (team2 or "")).strip()
        bike = next((b for b in BRANDS if b in blob.upper()), None)
        team2 = re.sub(r"\s*\b(" + BRAND_RE + r")\b\s*$", "", team2 or "").strip()
        out["results"].append(result_row(
            pos=int(pos) if pos else None, no=int(no), rider=name_team.strip(),
            nat=nat, team=(team2 or "").strip() or None, bike=bike,
            laps=int(laps), time=total, time_s=t2s(total),
            gap=None if gap in ("-", None) else gap.lstrip("+"), best_lap=blap,
            best_lap_s=t2s(blap), best_lap_no=int(blap_no), tyres=tyre,
            status="OK" if pos else "DNF"))
    if not out.get("date"):
        out["date"] = find_date(text)


def _booklet_practice_classification(text, out):
    # '2 57 FRANCISCO JAVIER PALOMERESP TEAM HONDA LAGLISSE HONDA Dunlop SBK
    #  1:38.722 13 14 +0.262 +0.262 152.5'
    tyre_re = "|".join(TYRES)
    tail = re.compile(
        r"\s+(\d:\d{2}\.\d{3})\s+(\d{1,2})\s+(\d{1,2})\s+"
        r"(-|\+[\d:.,]+)\s+(-|\+[\d:.,]+)\s+(\d{2,3}\.\d)\s*$")
    for raw in text.splitlines():
        line = raw.strip()
        tm = tail.search(line)
        if not tm:
            continue
        head = line[:tm.start()]
        hm = re.match(r"^(\d{1,2})\s+(\d{1,3})\s*(.+?)\s*"
                      r"(?:(" + tyre_re + r")\s+)?(\S+)$", head)
        if not hm:
            continue
        pos, no, blob, tyre, _cls = hm.groups()
        best, blap_no, laps, gap, _prev, _kmh = tm.groups()
        name, nat, team = split_rider_nat_team(blob)
        bike = next((b for b in BRANDS
                     if re.search(r"\b" + b, (team or "").upper())), None)
        if bike and team:
            team = re.sub(r"\s*\b" + bike + r"\S*\s*$", "", team,
                          flags=re.I).strip() or None
        out["results"].append(result_row(
            pos=int(pos), no=int(no), rider=name, nat=nat, team=team,
            bike=bike, laps=int(laps), gap=None if gap == "-" else gap.lstrip("+"),
            best_lap=best, best_lap_s=t2s(best), best_lap_no=int(blap_no),
            tyres=tyre))
    if not out.get("date"):
        out["date"] = find_date(text)


def _booklet_laps(text, out):
    # variants: [3-4 sectors] then (cum) | (speed) | (speed cum)
    lap_row = re.compile(
        r"^_{0,2}(\d{1,2})\s+(\d+:\d{2}\.\d{3})B?"
        r"((?:\s+\d{1,3}\.\d{3}){2,4})"
        r"(?:\s+(\d{2,3}[.,]\d{1,2}))?"
        r"(?:\s+(\d+:\d{2}\.\d{1,3})B?)?_{0,2}$")
    hdr = re.compile(r"^(\d{1,3})\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\.\'\- ]{3,}?)"
                     r"(?:\s+([A-Z]{2,3}))?\s*$")
    name_only = re.compile(r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\.\'\- ]{3,}?)\s+([A-Z]{2,3})\s*$")
    team_re = re.compile(r"^([A-ZÀ-Ú0-9&\.\'\/\- ]+?)\s+(" + BRAND_RE + r")\s*$")
    pending_name = None
    for raw in text.splitlines():
        line = raw.strip().replace("*", "")
        # 'ENZO ZARAGOZA ESP' followed by a bare number line '85'
        if pending_name and re.match(r"^\d{1,3}$", line):
            no = int(line)
            out["_cur_rider"] = no
            out.setdefault("_lap_riders", []).append(
                dict(no=no, name=pending_name[0], nat=pending_name[1]))
            pending_name = None
            continue
        pending_name = None
        m = hdr.match(line)
        if m and len(m.group(2).strip()) > 3 and " " in m.group(2).strip():
            no = int(m.group(1))
            out["_cur_rider"] = no
            out.setdefault("_lap_riders", []).append(
                dict(no=no, name=m.group(2).strip(), nat=m.group(3)))
            continue
        nm = name_only.match(line)
        if nm and nm.group(2) in NATS:
            pending_name = (nm.group(1).strip(), nm.group(2))
            continue
        tm = team_re.match(line)
        if tm and out.get("_cur_rider") is not None:
            for lr in out.get("_lap_riders", []):
                if lr["no"] == out["_cur_rider"] and "team" not in lr:
                    lr["team"], lr["bike"] = tm.group(1).strip(), tm.group(2)
            continue
        m = lap_row.match(line)
        if m and out.get("_cur_rider") is not None:
            lap, lt, secs_s, speed, cum = m.groups()
            secs = [t2s(s) for s in secs_s.split()]
            if speed is None and cum is None and len(secs) < 4:
                continue
            lt_s = t2s(lt)
            # lap 1 often lacks sector 1: pad at the front if sum is short
            if lt_s and sum(s for s in secs if s) < lt_s - 0.5:
                secs = [None] + secs
            _add_lap(out, out["_cur_rider"], int(lap), time=lt, time_s=lt_s,
                     sectors=secs, speed=fnum(speed))


def _booklet_vmax(text, out):
    pat = re.compile(
        r"^(\d{1,3})\s+(.+?)\s+(\S+)\s+(\d{2,3}\.\d)\s+(\d{1,2})\s+"
        r"(?:\d{2,3}\.\d\s+\d{1,2}\s+){4}[\d,\.]+\s*$")
    names = {r["no"]: r["rider"] for r in out["results"] if r["rider"]}
    for raw in text.splitlines():
        m = pat.match(raw.strip())
        if not m:
            continue
        no = int(m.group(1))
        rider = names.get(no)
        if rider is None:
            rider, _ = split_name_team(m.group(2), {}, no)
        _set_vmax(out, no, rider, float(m.group(4)), int(m.group(5)))


# --------------------------------------------------------------------------
# mastertiming
# --------------------------------------------------------------------------

def parse_mastertiming(pdf, pages_text, out):
    for t in pages_text:
        if re.search(r"\bResults\b", t[:400]) or "Best Lap" in t[:600]:
            _mt_classification(t, out)
            break
    out["_cur_rider"] = None
    for i, t in enumerate(pages_text):
        if "ANALYSIS" in t:
            for half_text in _halves_text(pdf.pages[i]):
                _mt_analysis(half_text, out)
    out.pop("_cur_rider", None)
    # vmax comes from per-lap speeds
    for lr in out["laps"]:
        speeds = [(l["speed"], l["lap"]) for l in lr["laps"] if l.get("speed")]
        if speeds:
            v, lap = max(speeds)
            rider = next((r["rider"] for r in out["results"]
                          if r["no"] == lr["no"]), None)
            _set_vmax(out, lr["no"], rider, v, lap)


def _mt_classification(text, out):
    # '2 44 ODENDAAL,Steven NEW2 PROJECT TEAM YAMAHA RSA 12 19:34,831 01:35,692 11 00:02,878 00:02,878 147,27 Pirell2i0SBK'
    pat = re.compile(
        r"^(\d{1,2}|\.)\s+(\d{1,3})\s+(\S[^,]*,\s*\S+(?:\s+[A-Z][a-z]+\.?|\s+[A-Z]\.)?)"
        r"\s+(.*?)\s+"
        r"([A-Z]{3})\s+(\d{1,2})\s+(\d{2}:\d{2},\d{3})\s+(\d{2}:\d{2},\d{3})\s+"
        r"(\d{1,2})\s+(?:(\d{2}:\d{2},\d{3}|-\d+\s*Laps?)\s+"
        r"(\d{2}:\d{2},\d{3}|-\d+\s*Laps?)\s+)?(\d{2,3},\d{1,2})\s*(.*)$")
    for raw in text.splitlines():
        line = raw.strip()
        m = pat.match(line)
        if not m:
            continue
        (pos, no, name, teambike, nat, laps, total, best, il, gap, _intv,
         _speed, _trail) = m.groups()
        if nat not in NATS:
            continue
        bike = next((b for b in BRANDS
                     if re.search(r"\b" + b + r"\b", teambike.upper())), None)
        team = teambike
        if bike:
            team = re.sub(r"\b" + bike + r"\b\s*$", "", teambike, flags=re.I).strip()
        tyre = next((t for t in TYRES
                     if t[:5] in re.sub(r"\d", "", line)), None)
        out["results"].append(result_row(
            pos=None if pos == "." else int(pos), no=int(no),
            rider=name.strip(), nat=nat, team=team or None, bike=bike,
            laps=int(laps), time=fmt(total), time_s=t2s(total),
            gap=fmt(gap) if gap and "Lap" not in (gap or "") else gap,
            best_lap=fmt(best), best_lap_s=t2s(best), best_lap_no=int(il),
            tyres=tyre, status="OK" if pos != "." else "DNF"))
    if not out.get("date"):
        out["date"] = find_date(text)


def _mt_analysis(text, out):
    row = re.compile(
        r"^(\d{1,2})\s+(?:FIRST LAP|(\d{2}:\d{2},\d{3}))\s+(\d{2}:\d{2},\d{3})\s+"
        r"(\d{2}:\d{2},\d{3})\s+(\d{2}:\d{2},\d{3})\s+(\d{2}:\d{2},\d{3})\s+"
        r"(\d{2,3},\d{1,2})\s+\d{1,2}:\d{2}:\d{2}\s*$")
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        nm = re.match(r"^([A-ZÀ-Ú][^,]*,\s*\S.*?)(?:\s{2,}.*)?$", line)
        if nm and "," in line and i + 1 < len(lines):
            nomatch = re.match(r"^(\d{1,3})\b", lines[i + 1].strip())
            if nomatch:
                no = int(nomatch.group(1))
                out["_cur_rider"] = no
                out.setdefault("_lap_riders", []).append(
                    dict(no=no, name=nm.group(1).strip()))
                continue
        m = row.match(line)
        if m and out.get("_cur_rider") is not None:
            lap, lt, s1, s2, s3, s4, vmax = m.groups()
            secs = [t2s(s1), t2s(s2), t2s(s3), t2s(s4)]
            _add_lap(out, out["_cur_rider"], int(lap),
                     time=lt if lt else None,
                     time_s=t2s(lt) if lt else round(sum(secs), 3),
                     sectors=secs, speed=fnum(vmax))


# --------------------------------------------------------------------------
# navarra
# --------------------------------------------------------------------------

def parse_navarra(pdf, pages_text, out):
    for t in pages_text:
        if "Clasificación" in t[:200] and re.search(r"\bPos\b", t):
            _nv_classification(t, out)
            if not out["results"]:
                _nv_practice_classification(t, out)
            break
    out["_cur_rider"] = None
    for i, t in enumerate(pages_text):
        if "T.Transc" in t and "MEJORES" not in t[:200]:
            for half_text in _halves_text(pdf.pages[i]):
                _nv_laps(half_text, out)
    out.pop("_cur_rider", None)
    for t in pages_text:
        if "MEJORES VELOCIDADES" in t and "POR PILOTO" not in t:
            _nv_vmax(t, out)
            break
    names = {lr["no"]: lr["name"] for lr in out.get("_lap_riders", [])}
    for r in out["results"]:
        if r["rider"]:
            rider, team = split_name_team(r["rider"], names, r["no"])
            if team or names.get(r["no"]):
                r["rider"] = rider
                if team and not r["team"]:
                    r["team"] = team
    out.pop("_lap_riders", None)


def _nv_classification(text, out):
    # '4 38 SUI ALESSIO ARNOLD FULLMOTO AC RACING JHONDA 4 Talent 14 27:26.161 5.396 0.016 1:56.427 5 132,050'
    # class may be multi-word ('Kawasaki Ni') or absent
    tail = re.compile(
        r"\s+(\d{1,2})\s+(\d+:\d{2}\.\d{3})\s+(?:([\d:.]+)\s+([\d:.]+)\s+)?"
        r"(\d:\d{2}\.\d{3})\s+(\d{1,2})\s+(\d{2,3},\d+)\s*$")
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        tm = tail.search(line)
        if not tm:
            continue
        hm = re.match(r"^(\d{1,2})\s+(\d{1,3})\s+(?:([A-Z]{3})\s+)?(.+)$",
                      line[:tm.start()])
        if not hm:
            continue
        pos, no, nat, blob = hm.groups()
        laps, total, dif, _prev, best, blap_no, _kmh = tm.groups()
        if nat and nat not in NATS:
            blob = nat + " " + blob
            nat = None
        # blob = 'NAME TEAM [BRAND] PC [Clase..]' -> strip PC + class words
        bm = re.match(r"^(.+?)\s+\d{1,3}(?:\s+\D.*)?$", blob)
        if bm:
            blob = bm.group(1)
        bike = None
        vm = re.search(r"(" + BRAND_RE + r")\s*$", blob)
        if vm:
            bike = vm.group(1)
            blob = blob[:vm.start()].rstrip()
            # column-concat leftovers like 'RACING T' from 'TKAWASAKI'
        out["results"].append(result_row(
            pos=int(pos), no=int(no), nat=nat, rider=blob.strip(), bike=bike,
            laps=int(laps), time=total, time_s=t2s(total), gap=dif,
            best_lap=best, best_lap_s=t2s(best), best_lap_no=int(blap_no)))
    if not out.get("date"):
        out["date"] = find_date(text)


def _nv_practice_classification(text, out):
    # 'Pos N Nat Nombre Equipo Vehiculo PC Clase V.Rap. Dif 1º Prev. Vtas V. Km/h'
    # '2 75 IVO LOPES BMW EASYRACE TEAM 2 SBK 1:45.667 0.624 0.624 14 11 115,281'
    tail = re.compile(
        r"\s+(\d:\d{2}\.\d{3})\s+(?:([\d:.]+)\s+([\d:.]+)\s+)?"
        r"(\d{1,2})\s+(\d{1,2})\s+(\d{2,3},\d+)\s*$")
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        tm = tail.search(line)
        if not tm:
            continue
        hm = re.match(r"^(\d{1,2})\s+(\d{1,3})\s+(.+?)\s+(\d{1,3})\s+(\S+)$",
                      line[:tm.start()])
        if not hm:
            continue
        pos, no, blob, _pc, _cls = hm.groups()
        best, dif, _prev, vtas, blap_no, _kmh = tm.groups()
        nat = None
        nm = re.match(r"^([A-Z]{3})\s+(.+)$", blob)
        if nm and nm.group(1) in NATS:
            nat, blob = nm.group(1), nm.group(2)
        bike = None
        bm = re.search(r"\s(" + BRAND_RE + r")\s*$", blob)
        if bm:
            bike, blob = bm.group(1), blob[:bm.start()]
        out["results"].append(result_row(
            pos=int(pos), no=int(no), nat=nat, rider=blob.strip(), bike=bike,
            laps=int(vtas), gap=dif, best_lap=best, best_lap_s=t2s(best),
            best_lap_no=int(blap_no)))
    if not out.get("date"):
        out["date"] = find_date(text)


def _nv_laps(text, out):
    hdr = re.compile(r"^(\d{1,3})\s+(.+?)\s+Clase:")
    row = re.compile(
        r"^(\d{1,2})\s+(\d+:\d{2}\.\d{3})\s+((?:\d{1,2}\.\d{3}\s+){2,4})"
        r"(\d{2,3}[.,]\d{1,3})\s+(\d+:\d{2}\.\d{3})\s*$")
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        m = hdr.match(line)
        if m:
            no = int(m.group(1))
            out["_cur_rider"] = no
            out.setdefault("_lap_riders", []).append(
                dict(no=no, name=m.group(2).strip()))
            continue
        m = row.match(line)
        if m and out.get("_cur_rider") is not None:
            lap, lt, secs_s, kmh, _cum = m.groups()
            secs = [t2s(s) for s in secs_s.split()]
            if len(secs) == 3:
                secs = [None] + secs
            _add_lap(out, out["_cur_rider"], int(lap), time=lt, time_s=t2s(lt),
                     sectors=secs[:4], speed=fnum(kmh))


def _nv_vmax(text, out):
    names = {r["no"]: r["rider"] for r in out["results"] if r["rider"]}
    for raw in text.splitlines():
        m = re.match(r"^(\d{1,3})\s+(.+?)\s+(\S+)\s+(\d{2,3}[.,]\d{1,3})\s+"
                     r"(\d{1,2})\s+\d+:\d{2}\.\d{3}\s*$",
                     re.sub(r"\s+", " ", raw).strip())
        if not m:
            continue
        no = int(m.group(1))
        _set_vmax(out, no, names.get(no, m.group(2).strip()),
                  fnum(m.group(4)), int(m.group(5)))


# --------------------------------------------------------------------------

PARSERS = {
    "cronojerez": parse_cronojerez,
    "booklet": parse_booklet,
    "mastertiming": parse_mastertiming,
    "navarra": parse_navarra,
}


def parse_pdf(path):
    out = dict(layout=None, date=None, results=[], laps=[], top_speeds=[],
               warnings=[])
    with pdfplumber.open(path) as pdf:
        pages_text = []
        for p in pdf.pages:
            try:
                pages_text.append(decode_pua(p.extract_text() or ""))
            except Exception as e:  # noqa: BLE001
                pages_text.append("")
                out["warnings"].append(f"page {p.page_number}: {e}")
        layout = detect_layout("\n".join(pages_text))
        out["layout"] = layout
        if layout is None:
            out["warnings"].append("unknown layout")
            return out
        try:
            PARSERS[layout](pdf, pages_text, out)
        except Exception as e:  # noqa: BLE001
            out["warnings"].append(f"parser error: {type(e).__name__}: {e}")
    for lr in out["laps"]:
        lr["laps"].sort(key=lambda l: l["lap"])
    # sanity: sector sums should match lap time
    bad = 0
    for lr in out["laps"]:
        for l in lr["laps"]:
            if l["time_s"] and l["sectors"] and all(l["sectors"]):
                if abs(sum(l["sectors"]) - l["time_s"]) > 0.05:
                    bad += 1
    if bad:
        out["warnings"].append(f"{bad} laps with sector/time mismatch")
    if not out["results"]:
        out["warnings"].append("no classification parsed")
    return out


if __name__ == "__main__":
    import json
    import sys
    res = parse_pdf(sys.argv[1])
    if len(sys.argv) > 2:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    r0 = res["results"][0] if res["results"] else None
    print("layout:", res["layout"], "| date:", res["date"],
          "| riders:", len(res["results"]), "| lap riders:", len(res["laps"]),
          "| vmax:", len(res["top_speeds"]), "| warnings:", res["warnings"])
    if r0:
        print("P1:", {k: v for k, v in r0.items() if v is not None})
