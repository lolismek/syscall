import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import type { TimeseriesPoint } from "../types.ts";
import { fmtTokens } from "../lib/format.ts";

interface Props {
  data: TimeseriesPoint[];
}

export default function TokenChart({ data }: Props) {
  return (
    <div className="bg-surface-50 border border-surface-200 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-400 mb-3">
        Total Tokens
      </h3>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="tokenGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
            </linearGradient>
          </defs>
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
            tickFormatter={fmtTokens}
          />
          <Tooltip
            contentStyle={{
              background: "#161922",
              border: "1px solid #252a3a",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#9ca3af" }}
            formatter={(value: number) => fmtTokens(value)}
          />
          <Area
            type="monotone"
            dataKey="total_tokens"
            stroke="#3b82f6"
            strokeWidth={2}
            fill="url(#tokenGrad)"
            name="Tokens"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
