import type { Run, Overview } from "../types.ts";

interface Props {
  runs: Run[];
  activeRunId: string | null;
  onRunChange: (id: string) => void;
  overview: Overview | null;
  lastRefresh: Date | null;
}

export default function Header({
  runs,
  activeRunId,
  onRunChange,
  overview,
  lastRefresh,
}: Props) {
  return (
    <header className="sticky top-0 z-50 border-b border-surface-200 bg-[#0d0f15]/80 backdrop-blur-xl">
      <div className="mx-auto max-w-[1600px] px-4 py-3 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="h-6 w-6 rounded-md bg-gradient-to-br from-accent to-info flex items-center justify-center text-[10px] font-bold text-white">
            S
          </div>
          <h1 className="text-base font-bold tracking-tight text-white">
            Syscall
          </h1>
        </div>

        <select
          value={activeRunId ?? ""}
          onChange={(e) => onRunChange(e.target.value)}
          className="bg-surface-100 border border-surface-200 text-gray-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-accent/50"
        >
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id.slice(0, 8)} &middot; {r.problem_id} &middot;{" "}
              {r.status}
            </option>
          ))}
        </select>

        {overview && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-accent/10 border border-accent/20 px-3 py-0.5 text-xs font-medium text-accent">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            {overview.status} &middot; {overview.problem_id}
          </span>
        )}

        <span className="ml-auto text-xs text-gray-500">
          {lastRefresh
            ? `refreshed ${lastRefresh.toLocaleTimeString()}`
            : "loading..."}
        </span>
      </div>
    </header>
  );
}
