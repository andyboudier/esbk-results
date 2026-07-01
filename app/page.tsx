import Link from "next/link";
import { facets, norm, searchSessions } from "@/lib/data";

export const dynamic = "force-dynamic";

const KINDS = [
  ["", "All sessions"],
  ["race", "Races"],
  ["qualifying", "Qualifying"],
  ["practice", "Practice"],
  ["warmup", "Warm up"],
] as const;

function fmtLap(s: number | null, str: string | null) {
  return str ?? (s ? s.toFixed(3) : "–");
}

export default async function Home(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await props.searchParams;
  const get = (k: string) => {
    const v = sp[k];
    return typeof v === "string" && v ? v : undefined;
  };
  const filters = {
    q: get("q"),
    circuit: get("circuit"),
    klass: get("class"),
    year: get("year"),
    kind: get("kind"),
    from: get("from"),
    to: get("to"),
  };
  const [rows, f] = await Promise.all([searchSessions(filters), facets()]);
  const shown = rows.slice(0, 250);
  const q = filters.q ? norm(filters.q) : null;

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">
          ESBK Results Explorer
        </h1>
        <p className="text-sm opacity-70">
          Campeonato de España de Superbike — race results, lap times, sector
          times and top speeds parsed from official RFME PDFs.
        </p>
      </header>

      <form
        method="GET"
        className="mb-6 grid grid-cols-2 gap-3 rounded-xl border border-neutral-300/40 p-4 md:grid-cols-7"
      >
        <input
          type="search"
          name="q"
          defaultValue={filters.q ?? ""}
          placeholder="Pilot name…"
          className="col-span-2 rounded-md border border-neutral-400/50 bg-transparent px-3 py-1.5 text-sm"
        />
        <select
          name="year"
          defaultValue={filters.year ?? ""}
          className="rounded-md border border-neutral-400/50 bg-transparent px-2 py-1.5 text-sm"
        >
          <option value="">Year</option>
          {f.years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        <select
          name="circuit"
          defaultValue={filters.circuit ?? ""}
          className="rounded-md border border-neutral-400/50 bg-transparent px-2 py-1.5 text-sm"
        >
          <option value="">Circuit</option>
          {f.circuits.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          name="class"
          defaultValue={filters.klass ?? ""}
          className="rounded-md border border-neutral-400/50 bg-transparent px-2 py-1.5 text-sm"
        >
          <option value="">Class</option>
          {f.classes.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          name="kind"
          defaultValue={filters.kind ?? ""}
          className="rounded-md border border-neutral-400/50 bg-transparent px-2 py-1.5 text-sm"
        >
          {KINDS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        <button
          type="submit"
          className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700"
        >
          Search
        </button>
        <div className="col-span-2 flex flex-wrap items-center gap-2 text-xs opacity-80 md:col-span-6">
          <label>
            From{" "}
            <input
              type="date"
              name="from"
              defaultValue={filters.from ?? ""}
              className="rounded-md border border-neutral-400/50 bg-transparent px-2 py-1"
            />
          </label>
          <label>
            To{" "}
            <input
              type="date"
              name="to"
              defaultValue={filters.to ?? ""}
              className="rounded-md border border-neutral-400/50 bg-transparent px-2 py-1"
            />
          </label>
          <span className="ml-auto">
            {rows.length} session{rows.length === 1 ? "" : "s"}
            {rows.length > shown.length ? ` (showing ${shown.length})` : ""}
          </span>
        </div>
      </form>

      <div className="overflow-x-auto rounded-xl border border-neutral-300/40">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide opacity-60">
            <tr>
              <th className="px-3 py-2">Date</th>
              <th className="px-3 py-2">Circuit</th>
              <th className="px-3 py-2">Class</th>
              <th className="px-3 py-2">Session</th>
              <th className="px-3 py-2">
                {q ? "Pilot result" : "Winner / fastest"}
              </th>
              <th className="px-3 py-2 text-right">Best lap</th>
              <th className="px-3 py-2 text-right">Vmax km/h</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => {
              const rider = q
                ? s.riders.find((r) => r.rider && norm(r.rider).includes(q))
                : (s.riders.find((r) => r.pos === 1) ?? s.riders[0]);
              return (
                <tr
                  key={s.id}
                  className="border-t border-neutral-300/30 hover:bg-neutral-500/5"
                >
                  <td className="whitespace-nowrap px-3 py-2">{s.date ?? "–"}</td>
                  <td className="px-3 py-2">{s.circuit}</td>
                  <td className="whitespace-nowrap px-3 py-2">{s.class}</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <Link
                      href={`/session/${s.id}`}
                      className="font-medium text-red-600 hover:underline"
                    >
                      {s.session}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    {rider ? (
                      <>
                        {rider.pos ? `P${rider.pos} ` : ""}
                        <span className="font-medium">
                          #{rider.no} {rider.rider ?? "?"}
                        </span>
                      </>
                    ) : (
                      "–"
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {rider ? fmtLap(rider.best_lap_s, rider.best_lap) : "–"}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {rider?.top_speed ? rider.top_speed.toFixed(1) : "–"}
                  </td>
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center opacity-60">
                  No sessions match those filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <footer className="mt-6 text-xs opacity-50">
        Data source:{" "}
        <a
          href="https://rfme.com/campeonatos/campeonato-de-espana-de-superbike/"
          className="underline"
        >
          RFME ESBK official results
        </a>
        . Times as published; parsing errors possible.
      </footer>
    </main>
  );
}
