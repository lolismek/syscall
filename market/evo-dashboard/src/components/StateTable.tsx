import type { StateCounts } from "../types.ts";

interface Props {
  states: StateCounts;
}

const stateColors: Record<string, string> = {
  scored: "text-accent",
  benchmarked: "text-info",
  validated: "text-cyan-400",
  built: "text-yellow-400",
  generated: "text-gray-400",
  failed: "text-red-400",
};

export default function StateTable({ states }: Props) {
  const entries = Object.entries(states.state_counts).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  const total = entries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className="bg-surface-50 border border-surface-200 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-400 mb-3">
        Candidate States
      </h3>
      <div className="space-y-2">
        {entries.map(([state, count]) => {
          const pct = total > 0 ? (count / total) * 100 : 0;
          return (
            <div key={state}>
              <div className="flex items-center justify-between text-xs mb-0.5">
                <span className={stateColors[state] ?? "text-gray-400"}>
                  {state}
                </span>
                <span className="text-gray-500 tabular-nums">{count}</span>
              </div>
              <div className="h-1.5 w-full bg-surface-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent/60 rounded-full transition-all duration-500"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      <div className="text-xs text-gray-500 mt-2 text-right">
        {total} total candidates
      </div>
    </div>
  );
}
