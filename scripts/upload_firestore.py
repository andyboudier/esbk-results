"""Upload parsed sessions to Google Firestore.

Credentials: set FIREBASE_SERVICE_ACCOUNT to the service-account JSON
(the whole JSON string, e.g. a GitHub Actions secret), or set
GOOGLE_APPLICATION_CREDENTIALS to a key-file path.

Collection layout:
  sessions/{session_id}   - full session document (results, laps, top speeds)

Only sessions changed since the last upload are written (tracked via a
`_meta/upload` doc holding content hashes).

Usage:
  python scripts/upload_firestore.py            # incremental
  python scripts/upload_firestore.py --all      # rewrite everything
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "data" / "sessions"


def get_client():
    from google.cloud import firestore
    from google.oauth2 import service_account

    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if raw:
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info)
        return firestore.Client(project=info["project_id"], credentials=creds)
    return firestore.Client()  # GOOGLE_APPLICATION_CREDENTIALS / ADC


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("FIREBASE_SERVICE_ACCOUNT") and \
            not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("No Firebase credentials configured "
              "(FIREBASE_SERVICE_ACCOUNT / GOOGLE_APPLICATION_CREDENTIALS); "
              "skipping Firestore upload.")
        return 0

    db = get_client()
    meta_ref = db.collection("_meta").document("upload")
    known = {} if args.all else (meta_ref.get().to_dict() or {}).get("hashes", {})

    files = sorted(SESSIONS.glob("*.json"))
    written = 0
    batch = db.batch()
    pending = 0
    for f in files:
        raw = f.read_text()
        h = hashlib.sha1(raw.encode()).hexdigest()
        sid = f.stem
        if known.get(sid) == h:
            continue
        doc = json.loads(raw)
        batch.set(db.collection("sessions").document(sid), doc)
        known[sid] = h
        written += 1
        pending += 1
        if pending >= 200:  # Firestore batch limit is 500 ops
            batch.commit()
            batch = db.batch()
            pending = 0
    if pending:
        batch.commit()
    meta_ref.set({"hashes": known})
    print(f"Firestore: {written} sessions written, {len(files)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
