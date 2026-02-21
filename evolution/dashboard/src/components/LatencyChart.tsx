import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from "recharts";
import type { TimeseriesPoint } from "../types.ts";
import { getReferences } from "../lib/references.ts";

interface Props {
  data: TimeseriesPoint[];
  problemId: string;
}

export default function LatencyChart({ data, problemId }: Props) {
  const refs = getReferences(problemId);

  return (
    <div className="bg-surface-50 border border-surface-200 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-400 mb-3">
        Best Median Latency (us) Over Iterations
      </h3>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" />
          <XAxis
            dataKey="iteration"
            stroke="#4b5563"
            fontSize={11}
            tickLine={false}
          />
          <YAxis
            stroke="#4b5563"
            fontSize={11}
            tickLine={false}
            reversed
          />
          <Tooltip
            contentStyle={{
              background: "#161922",
              border: "1px solid #252a3a",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#9ca3af" }}
            formatter={(value: number) =>
              value != null ? `${value.toFixed(1)} us` : "n/a"
            }
          />
          {refs?.latency.map((ref) => (
            <ReferenceLine
              key={ref.label}
              y={ref.value}
              stroke={ref.color}
              strokeDasharray="6 4"
              label={{
                value: `${ref.label} ${ref.value}`,
                fill: ref.color,
                fontSize: 10,
                position: "right",
              }}
            />
          ))}
          <Line
            type="monotone"
            dataKey="best_representative_median_us"
            stroke="#f97316"
            strokeWidth={2}
            dot={false}
            name="Best Median"
          />
          <Line
            type="monotone"
            dataKey="best_quick_median_us"
            stroke="#f97316"
            strokeWidth={1}
            strokeOpacity={0.3}
            dot={false}
            name="Quick"
          />
          <Line
            type="monotone"
            dataKey="best_full_median_us"
            stroke="#fb923c"
            strokeWidth={1.5}
            strokeDasharray="4 2"
            dot={false}
            name="Full"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
