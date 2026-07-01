import fs from "node:fs/promises";
import path from "node:path";

export type ResultRow = {
  pos: number | null;
  no: number;
  rider: string | null;
  nat: string | null;
  team: string | null;
  bike: string | null;
  laps: number | null;
  time: string | null;
  time_s: number | null;
  gap: string | null;
  best_lap: string | null;
  best_lap_s: number | null;
  best_lap_no: number | null;
  top_speed: number | null;
  tyres: string | null;
  status: string;
};

export type Lap = {
  lap: number;
  time: string | null;
  time_s: number | null;
  sectors: (number | null)[] | null;
  speed: number | null;
};

export type SessionDoc = {
  id: string;
  year: number;
  round: number | null;
  circuit: string;
  event_slug: string;
  class: string;
  session: string;
  session_kind: string;
  date: string | null;
  pdf_url: string;
  layout: string | null;
  results: ResultRow[];
  laps: { no: number; laps: Lap[] }[];
  top_speeds: { no: number; rider: string | null; vmax: number; lap: number | null }[];
};

export type IndexRow = {
  id: string;
  year: number;
  round: number | null;
  circuit: string;
  class: string;
  session: string;
  session_kind: string;
  date: string | null;
  riders: {
    no: number;
    rider: string | null;
    pos: number | null;
    best_lap_s: number | null;
    best_lap: string | null;
    top_speed: number | null;
    status: string;
  }[];
};

const DATA_DIR = path.join(process.cwd(), "data");

let indexCache: IndexRow[] | null = null;

export async function loadIndex(): Promise<IndexRow[]> {
  if (indexCache) return indexCache;
  const raw = await fs.readFile(path.join(DATA_DIR, "index.json"), "utf8");
  indexCache = JSON.parse(raw) as IndexRow[];
  return indexCache;
}

export async function loadSession(id: string): Promise<SessionDoc | null> {
  if (!/^[a-z0-9-]+$/.test(id)) return null;
  try {
    const raw = await fs.readFile(
      path.join(DATA_DIR, "sessions", `${id}.json`),
      "utf8",
    );
    return JSON.parse(raw) as SessionDoc;
  } catch {
    return null;
  }
}

export type Filters = {
  q?: string; // pilot name
  circuit?: string;
  klass?: string;
  year?: string;
  kind?: string;
  from?: string;
  to?: string;
};

export function norm(s: string): string {
  return s
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

export async function searchSessions(f: Filters) {
  const idx = await loadIndex();
  const q = f.q ? norm(f.q) : null;
  const rows = idx.filter((s) => {
    if (f.year && String(s.year) !== f.year) return false;
    if (f.circuit && s.circuit !== f.circuit) return false;
    if (f.klass && s.class !== f.klass) return false;
    if (f.kind && s.session_kind !== f.kind) return false;
    if (f.from && (!s.date || s.date < f.from)) return false;
    if (f.to && (!s.date || s.date > f.to)) return false;
    if (q && !s.riders.some((r) => r.rider && norm(r.rider).includes(q)))
      return false;
    return true;
  });
  rows.sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "") || a.id.localeCompare(b.id));
  return rows;
}

export async function facets() {
  const idx = await loadIndex();
  const years = new Set<number>();
  const circuits = new Set<string>();
  const classes = new Set<string>();
  for (const s of idx) {
    years.add(s.year);
    circuits.add(s.circuit);
    classes.add(s.class);
  }
  return {
    years: [...years].sort((a, b) => b - a),
    circuits: [...circuits].sort(),
    classes: [...classes].sort(),
  };
}
