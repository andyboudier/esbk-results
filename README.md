# ESBK Results Explorer

Searchable database of **Campeonato de España de Superbike (ESBK)** results,
built from the official RFME PDF result sheets: classification, lap-by-lap
times, sector times and top speeds for every class and session since 2021.

- **Frontend**: Next.js (App Router) — deployed on Vercel
- **Data**: JSON in `data/` (committed) + optional Google Firestore mirror
- **Source**: <https://rfme.com/campeonatos/campeonato-de-espana-de-superbike/>

## How it works

```
RFME page ──▶ scripts/scrape_inventory.py ──▶ data/inventory.json
                     │  (event / circuit / date / class / session per PDF)
                     ▼
              scripts/ingest.py ──▶ data/sessions/*.json + data/index.json
                     │  downloads PDFs, parses 4 timing-provider layouts
                     ▼
              scripts/upload_firestore.py ──▶ Firestore `sessions` collection
```

The PDF parser (`scripts/parse_pdf.py`) understands the four timing systems
used across ESBK circuits:

| Layout        | Circuits                                   | Data extracted |
| ------------- | ------------------------------------------ | -------------- |
| `cronojerez`  | Jerez                                      | classification, laps, 4–5 sectors, Vmax |
| `booklet`     | Estoril, MotorLand, Barcelona, Navarra ≤25 | classification, laps, 3–4 sectors, Vmax |
| `mastertiming`| Ricardo Tormo (Valencia)                   | classification, laps, 4 sectors, per-lap Vmax |
| `navarra`     | Navarra 2026+                              | classification, laps, 4 sectors, per-lap Vmax |

Unparseable PDFs are logged in `data/ingest_report.json` and never crash a run.

## Local development

```bash
npm install
npm run dev            # http://localhost:3000

# Python pipeline
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements.txt
.venv/bin/python scripts/scrape_inventory.py    # refresh inventory
.venv/bin/python scripts/ingest.py              # process new PDFs only
.venv/bin/python scripts/ingest.py --retry-failed   # retry past failures
```

## Automated updates

`.github/workflows/update.yml` runs on a schedule (Sunday evening + Monday
morning UTC), re-scrapes the RFME page, ingests any newly published PDFs,
commits the updated JSON (which triggers a Vercel redeploy) and — when the
`FIREBASE_SERVICE_ACCOUNT` secret is set — mirrors changed sessions to
Firestore.

## Firestore setup (optional)

1. Create a Firebase project → Firestore database.
2. Create a service account key (Project settings → Service accounts).
3. Add the JSON as the `FIREBASE_SERVICE_ACCOUNT` GitHub Actions secret.

Documents land in `sessions/{session_id}` with embedded `results`, `laps`
and `top_speeds`.

## Data model

`data/sessions/<id>.json`:

```jsonc
{
  "id": "2026-01-circuito-de-jerez-angel-nieto-supersport-300-carrera-1",
  "year": 2026, "round": 1, "circuit": "Circuito de Jerez Ángel Nieto",
  "class": "Supersport 300", "session": "Carrera 1", "session_kind": "race",
  "date": "2026-03-21", "pdf_url": "…", "layout": "cronojerez",
  "results": [{ "pos": 1, "no": 99, "rider": "Marc VICH", "team": "BOX 77",
                "bike": "KAWASAKI", "laps": 12, "time": "23:15.609",
                "best_lap": "1:55.066", "best_lap_no": 6,
                "top_speed": 190.2, "tyres": "Dunlop", "status": "OK" }],
  "laps": [{ "no": 99, "laps": [{ "lap": 1, "time": "1:58.724",
             "sectors": [32.884, 16.863, 34.045, 34.932], "speed": null }] }],
  "top_speeds": [{ "no": 29, "rider": "Tobias Belton MAYER",
                   "vmax": 194.2, "lap": 1 }]
}
```
