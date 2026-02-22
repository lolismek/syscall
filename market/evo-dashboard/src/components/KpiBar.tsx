import type { Overview, Timeseries } from "../types.ts";
import { shortId, fmt, fmtTokens } from "../lib/format.ts";

interface Props {
  overview: Overview;
  timeseries: Timeseries;
}

interface Kpi {
  label: string;
  value: string;
  color?: string;
}

function readSpeedup(raw: Record<string, unknown> | undefined | null): number | null {
  if (!raw) return null;
  const value = raw.speedup_vs_ref;
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
}

export default function KpiBar({ overview, timeseries }: Props) {
  const global = timeseries.global;
  const latest = global.length ? global[global.length - 1] : null;
  const bestFullSpeedup = readSpeedup(overview.best?.full?.raw_score);
  const bestQuickSpeedup = readSpeedup(overview.best?.quick?.raw_score);

  const kpis: Kpi[] = [
    { label: "Run", value: shortId(overview.run_id) },
    { label: "Problem", value: overview.problem_id },
    {
      label: "Best Full Fitness",
      value: fmt(overview.best?.full?.scalar_fitness ?? null, 2),
      color: "text-accent",
    },
    {
      label: "Best Full Median",
      value:
        latest?.best_full_median_us != null
          ? `${fmt(latest.best_full_median_us, 1)} us`
          : "n/a",
      color: "text-warn",
    },
    {
      label: "Best Full Speedup",
      value: bestFullSpeedup != null ? `${fmt(bestFullSpeedup, 3)}x` : "n/a",
      color: "text-info",
    },
    {
      label: "Best Quick Fitness",
      value: fmt(overview.best?.quick?.scalar_fitness ?? null, 2),
      color: "text-accent",
    },
    {
      label: "Best Quick Median",
      value:
        latest?.best_quick_median_us != null
          ? `${fmt(latest.best_quick_median_us, 1)} us`
          : "n/a",
      color: "text-warn",
    },
    {
      label: "Best Quick Speedup",
      value: bestQuickSpeedup != null ? `${fmt(bestQuickSpeedup, 3)}x` : "n/a",
      color: "text-info",
    },
    {
      label: "Iteration",
      value: latest ? String(latest.iteration) : "n/a",
    },
    {
      label: "Total Tokens",
      value: latest ? fmtTokens(latest.total_tokens) : "0",
      color: "text-info",
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-10 gap-3">
      {kpis.map((kpi) => (
        <div
          key={kpi.label}
          className="bg-surface-50 backdrop-blur-xl border border-surface-200 rounded-xl px-3 py-2.5 flex flex-col items-center justify-center text-center min-h-[64px]"
        >
          <div
            className={`text-lg font-bold tabular-nums leading-tight break-words w-full ${kpi.color ?? "text-white"}`}
          >
            {kpi.value}
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5">{kpi.label}</div>
        </div>
      ))}
    </div>
  );
}
