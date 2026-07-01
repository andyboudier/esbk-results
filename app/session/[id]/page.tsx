import Link from "next/link";
import { notFound } from "next/navigation";
import { loadSession } from "@/lib/data";

export const dynamic = "force-dynamic";

function fmtSec(v: number | null | undefined) {
  return v == null ? "–" : v.toFixed(3);
}

export default async function SessionPage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;
  const doc = await loadSession(id);
  if (!doc) notFound();

  const lapsByNo = new Map(doc.laps.map((l) => [l.no, l.laps]));
  const riderName = new Map(
    doc.results.map((r) => [r.no, r.rider ?? `#${r.no}`]),
  );
  const nSectors = Math.max(
    0,
    ...doc.laps.flatMap((lr) => lr.laps.map((l) => l.sectors?.length ?? 0)),
  );
  const hasLapSpeed = doc.laps.some((lr) => lr.laps.some((l) => l.speed));

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <nav className="mb-4 text-sm">
        <Link href="/" className="text-red-600 hover:underline">
          ← All sessions
        </Link>
      </nav>
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">
          {doc.class} — {doc.session}
        </h1>
        <p className="text-sm opacity-70">
          {doc.circuit}
          {doc.date ? ` · ${doc.date}` : ""} · {doc.year}
          {doc.round ? ` · Round ${doc.round}` : ""} ·{" "}
          <a href={doc.pdf_url} className="underline">
            official PDF
          </a>
        </p>
      </header>

      <h2 className="mb-2 text-lg font-semibold">Classification</h2>
      <div className="overflow-x-auto rounded-xl border border-neutral-300/40">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide opacity-60">
            <tr>
              <th className="px-3 py-2">Pos</th>
              <th className="px-3 py-2">#</th>
              <th className="px-3 py-2">Pilot</th>
              <th className="px-3 py-2">Team</th>
              <th className="px-3 py-2">Bike</th>
              <th className="px-3 py-2 text-right">Laps</th>
              <th className="px-3 py-2 text-right">Time</th>
              <th className="px-3 py-2 text-right">Gap</th>
              <th className="px-3 py-2 text-right">Best lap</th>
              <th className="px-3 py-2 text-right">Vmax</th>
            </tr>
          </thead>
          <tbody>
            {doc.results.map((r, i) => (
              <tr key={i} className="border-t border-neutral-300/30">
                <td className="px-3 py-1.5">
                  {r.status !== "OK" ? r.status : (r.pos ?? "–")}
                </td>
                <td className="px-3 py-1.5 font-medium">{r.no || "–"}</td>
                <td className="px-3 py-1.5 font-medium">
                  {r.rider ?? "?"}
                  {r.nat ? (
                    <span className="ml-1 text-xs opacity-60">{r.nat}</span>
                  ) : null}
                </td>
                <td className="px-3 py-1.5 opacity-80">{r.team ?? ""}</td>
                <td className="px-3 py-1.5 opacity-80">{r.bike ?? ""}</td>
                <td className="px-3 py-1.5 text-right">{r.laps ?? "–"}</td>
                <td className="px-3 py-1.5 text-right font-mono">
                  {r.time ?? "–"}
                </td>
                <td className="px-3 py-1.5 text-right font-mono">
                  {r.gap ?? ""}
                </td>
                <td className="px-3 py-1.5 text-right font-mono">
                  {r.best_lap ?? "–"}
                  {r.best_lap_no ? (
                    <span className="ml-1 text-xs opacity-60">
                      L{r.best_lap_no}
                    </span>
                  ) : null}
                </td>
                <td className="px-3 py-1.5 text-right font-mono">
                  {r.top_speed ? r.top_speed.toFixed(1) : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {doc.laps.length > 0 && (
        <>
          <h2 className="mb-2 mt-8 text-lg font-semibold">
            Lap times{nSectors ? " & sectors" : ""}
          </h2>
          <div className="space-y-2">
            {doc.laps.map((lr) => (
              <details
                key={lr.no}
                className="rounded-xl border border-neutral-300/40"
              >
                <summary className="cursor-pointer px-4 py-2 text-sm font-medium hover:bg-neutral-500/5">
                  #{lr.no} {riderName.get(lr.no) ?? ""}{" "}
                  <span className="opacity-60">
                    · {lr.laps.length} laps · best{" "}
                    {fmtSecToLap(
                      Math.min(
                        ...lr.laps
                          .filter((l) => l.time_s)
                          .map((l) => l.time_s as number),
                      ),
                    )}
                  </span>
                </summary>
                <div className="overflow-x-auto px-2 pb-3">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs uppercase opacity-60">
                      <tr>
                        <th className="px-2 py-1">Lap</th>
                        <th className="px-2 py-1 text-right">Time</th>
                        {Array.from({ length: nSectors }, (_, i) => (
                          <th key={i} className="px-2 py-1 text-right">
                            S{i + 1}
                          </th>
                        ))}
                        {hasLapSpeed && (
                          <th className="px-2 py-1 text-right">km/h</th>
                        )}
                      </tr>
                    </thead>
                    <tbody className="font-mono">
                      {lr.laps.map((l) => (
                        <tr
                          key={l.lap}
                          className="border-t border-neutral-300/20"
                        >
                          <td className="px-2 py-1">{l.lap}</td>
                          <td className="px-2 py-1 text-right">
                            {l.time ?? "–"}
                          </td>
                          {Array.from({ length: nSectors }, (_, i) => (
                            <td key={i} className="px-2 py-1 text-right">
                              {fmtSec(l.sectors?.[i])}
                            </td>
                          ))}
                          {hasLapSpeed && (
                            <td className="px-2 py-1 text-right">
                              {l.speed ? l.speed.toFixed(1) : "–"}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </div>
        </>
      )}

      {doc.top_speeds.length > 0 && (
        <>
          <h2 className="mb-2 mt-8 text-lg font-semibold">Top speeds</h2>
          <div className="overflow-x-auto rounded-xl border border-neutral-300/40">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide opacity-60">
                <tr>
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Pilot</th>
                  <th className="px-3 py-2 text-right">Vmax km/h</th>
                  <th className="px-3 py-2 text-right">Lap</th>
                </tr>
              </thead>
              <tbody>
                {[...doc.top_speeds]
                  .sort((a, b) => b.vmax - a.vmax)
                  .map((t, i) => (
                    <tr key={i} className="border-t border-neutral-300/30">
                      <td className="px-3 py-1.5">{t.no}</td>
                      <td className="px-3 py-1.5 font-medium">
                        {t.rider ?? riderName.get(t.no) ?? "?"}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono">
                        {t.vmax.toFixed(1)}
                      </td>
                      <td className="px-3 py-1.5 text-right">{t.lap ?? "–"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </main>
  );
}

function fmtSecToLap(s: number) {
  if (!isFinite(s)) return "–";
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return m
    ? `${m}:${rest.toFixed(3).padStart(6, "0")}`
    : rest.toFixed(3);
}
