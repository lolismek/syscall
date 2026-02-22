import type { LeaderboardRow } from "../types.ts";
import { shortId, fmt } from "../lib/format.ts";

interface Props {
  title: string;
  rows: LeaderboardRow[];
}

function readMetric(rawScore: Record<string, unknown>, key: string): number | null {
  const value = rawScore[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

export default function Leaderboard({ title, rows }: Props) {
  return (
    <div className="bg-surface-50 border border-surface-200 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-400 mb-3">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-surface-200">
              <th className="text-left py-2 px-2 font-medium">#</th>
              <th className="text-left py-2 px-2 font-medium">Candidate</th>
              <th className="text-right py-2 px-2 font-medium">Fitness</th>
              <th className="text-right py-2 px-2 font-medium">Median us</th>
              <th className="text-right py-2 px-2 font-medium">Speedup</th>
              <th className="text-left py-2 px-2 font-medium">State</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const rawScore = r.raw_score || {};
              const median = readMetric(rawScore, "median_us");
              const speedup = readMetric(rawScore, "speedup_vs_ref");
              return (
                <tr
                  key={r.candidate_id}
                  className={`border-b border-surface-200/50 ${i === 0 ? "bg-accent/5" : "hover:bg-surface-100/50"}`}
                >
                  <td className="py-1.5 px-2 text-gray-500">{i + 1}</td>
                  <td className="py-1.5 px-2 font-mono text-gray-300">
                    {shortId(r.candidate_id)}
                  </td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-accent">
                    {fmt(r.scalar_fitness, 2)}
                  </td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-warn">
                    {fmt(median, 1)}
                  </td>
                  <td className="py-1.5 px-2 text-right tabular-nums text-info">
                    {speedup != null && speedup > 0 ? `${fmt(speedup, 3)}x` : "n/a"}
                  </td>
                  <td className="py-1.5 px-2">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        r.state === "scored"
                          ? "bg-accent/10 text-accent"
                          : r.state === "failed"
                            ? "bg-red-500/10 text-red-400"
                            : "bg-surface-200 text-gray-400"
                      }`}
                    >
                      {r.state || "n/a"}
                    </span>
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="py-4 text-center text-gray-500">
                  No candidates yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
