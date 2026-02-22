import type { Overview } from "../types.ts";

interface Props {
  overview: Overview | null;
  lastRefresh: Date | null;
}

export default function Header({
  overview,
  lastRefresh,
}: Props) {
  return (
    <div className="hero">
      <div className="flex items-center gap-3 mb-2">
        <a
          href="/"
          className="text-[13px] font-medium hover:opacity-80 transition-opacity"
          style={{ color: "#6366f1" }}
        >
          &larr; Projects
        </a>
        <span style={{ color: "#71717a", fontSize: 12 }}>/</span>
        <span className="text-[13px] font-medium text-white">
          {overview?.problem_id ?? "Evolution"}
        </span>
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        <div>
          <div className="text-xl font-bold tracking-tight">
            Evolution Dashboard
          </div>
          <div className="text-[13px]" style={{ color: "#71717a", marginTop: -2 }}>
            KernelSwarm optimization
          </div>
        </div>

        {overview && (
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-3 py-0.5 text-[11px] font-semibold uppercase"
            style={{
              background: "#22c55e22",
              color: "#22c55e",
              letterSpacing: "0.5px",
            }}
          >
            <span
              className="h-1.5 w-1.5 rounded-full animate-pulse"
              style={{ background: "#22c55e" }}
            />
            {overview.status}
          </span>
        )}

        <span className="ml-auto text-xs" style={{ color: "#71717a" }}>
          {lastRefresh
            ? `refreshed ${lastRefresh.toLocaleTimeString()}`
            : "loading..."}
        </span>
      </div>
    </div>
  );
}
