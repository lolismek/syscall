import {
  ResponsiveContainer,
  LineChart,
  Line,
  YAxis,
} from "recharts";
import type { IslandPoint } from "../types.ts";
import { fmt } from "../lib/format.ts";

interface Props {
  islands: Record<string, IslandPoint[]>;
}

export default function IslandGrid({ islands }: Props) {
  const ids = Object.keys(islands).sort();

  if (ids.length === 0) {
    return <p className="text-sm text-gray-500">No island data yet</p>;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {ids.map((id) => {
        const pts = islands[id] || [];
        const latest = pts.length ? pts[pts.length - 1] : null;
        return (
          <div
            key={id}
            className="border border-surface-200 rounded-lg p-3 bg-surface/50"
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-semibold text-gray-300">
                {id}
              </span>
              <span className="text-[10px] text-gray-500">
                bins={latest?.occupied_bins ?? "?"} cov=
                {fmt(latest?.coverage_ratio ?? null, 3)}
              </span>
            </div>
            <ResponsiveContainer width="100%" height={60}>
              <LineChart data={pts}>
                <YAxis hide domain={["auto", "auto"]} />
                <Line
                  type="monotone"
                  dataKey="top_fitness"
                  stroke="#22c55e"
                  strokeWidth={1.5}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
