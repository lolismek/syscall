import { useMemo, useState } from "react";
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

interface BucketPoint {
  minute: string; // "HH:MM" label
  minuteTs: number; // epoch ms for sorting
  avg_median_us: number | null;
  best_full_median_us: number | null;
}

function bucketByMinute(data: TimeseriesPoint[]): BucketPoint[] {
  // Group points by minute using created_at
  const buckets = new Map<
    string,
    { medians: number[]; ts: number }
  >();

  for (const pt of data) {
    if (!pt.created_at) continue;
    const d = new Date(pt.created_at);
    if (isNaN(d.getTime())) continue;

    // Bucket key: truncate to minute
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

    if (!buckets.has(key)) {
      buckets.set(key, { medians: [], ts: d.getTime() });
    }
    const bucket = buckets.get(key)!;

    // Use representative median: full if available, else quick
    const median = pt.full_median_us ?? pt.quick_median_us;
    if (median != null && median > 0) {
      bucket.medians.push(median);
    }
  }

  // Sort by time, compute averages, and track running best full leader
  const sorted = [...buckets.entries()].sort((a, b) => a[1].ts - b[1].ts);

  let runningBestFull: number | null = null;
  let startTs: number | null = null;

  // Also need to compute running best from the raw data ordered by time
  // Collect all full_median_us values with timestamps for running best
  const fullMedians: { ts: number; val: number }[] = [];
  for (const pt of data) {
    if (!pt.created_at) continue;
    const d = new Date(pt.created_at);
    if (isNaN(d.getTime())) continue;
    if (pt.full_median_us != null && pt.full_median_us > 0) {
      fullMedians.push({ ts: d.getTime(), val: pt.full_median_us });
    }
  }
  fullMedians.sort((a, b) => a.ts - b.ts);

  // Build running best full by minute bucket
  const bestFullByMinute = new Map<string, number>();
  let runBest: number | null = null;
  let fmIdx = 0;

  for (const [key, bucket] of sorted) {
    const minuteEnd = bucket.ts + 60_000;
    while (fmIdx < fullMedians.length && fullMedians[fmIdx].ts < minuteEnd) {
      if (runBest === null || fullMedians[fmIdx].val < runBest) {
        runBest = fullMedians[fmIdx].val;
      }
      fmIdx++;
    }
    if (runBest !== null) {
      bestFullByMinute.set(key, runBest);
    }
  }

  const result: BucketPoint[] = [];
  for (const [key, bucket] of sorted) {
    if (startTs === null) startTs = bucket.ts;
    const d = new Date(bucket.ts);
    const label = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

    const avg =
      bucket.medians.length > 0
        ? bucket.medians.reduce((a, b) => a + b, 0) / bucket.medians.length
        : null;

    result.push({
      minute: label,
      minuteTs: bucket.ts,
      avg_median_us: avg,
      best_full_median_us: bestFullByMinute.get(key) ?? null,
    });
  }

  return result;
}

export default function LatencyChart({ data, problemId }: Props) {
  const refs = getReferences(problemId);
  const bucketData = useMemo(() => bucketByMinute(data), [data]);
  const [logScale, setLogScale] = useState(false);

  // Compute Y domain with some padding
  const yDomain = useMemo(() => {
    const vals: number[] = [];
    for (const pt of bucketData) {
      if (pt.avg_median_us != null) vals.push(pt.avg_median_us);
      if (pt.best_full_median_us != null) vals.push(pt.best_full_median_us);
    }
    if (vals.length === 0) return undefined;
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    if (logScale) {
      const logMin = min > 0 ? min : 1;
      return [logMin * 0.8, max * 1.2] as [number, number];
    }
    const padding = (max - min) * 0.1 || max * 0.1;
    return [Math.max(0, min - padding), max + padding] as [number, number];
  }, [bucketData, logScale]);

  return (
    <div className="bg-surface-50 border border-surface-200 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-400">
          Eval Latency Over Time (per minute)
        </h3>
        <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={logScale}
            onChange={(e) => setLogScale(e.target.checked)}
            className="accent-orange-500 w-3 h-3"
          />
          Log scale
        </label>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={bucketData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" />
          <XAxis
            dataKey="minute"
            stroke="#4b5563"
            fontSize={11}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke="#4b5563"
            fontSize={11}
            tickLine={false}
            scale={logScale ? "log" : "auto"}
            domain={yDomain}
            allowDataOverflow={logScale}
            tickFormatter={(v: number) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)}ms` : `${v.toFixed(0)}us`
            }
          />
          <Tooltip
            contentStyle={{
              background: "#161922",
              border: "1px solid #252a3a",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#9ca3af" }}
            formatter={(value: number, name: string) =>
              value != null
                ? [
                    `${value.toFixed(1)} us`,
                    name === "avg_median_us"
                      ? "Avg Eval (this minute)"
                      : "Best Full Leader",
                  ]
                : ["n/a", name]
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
            dataKey="avg_median_us"
            stroke="#818cf8"
            strokeWidth={1.5}
            dot={false}
            name="avg_median_us"
            connectNulls
          />
          <Line
            type="stepAfter"
            dataKey="best_full_median_us"
            stroke="#f97316"
            strokeWidth={2}
            dot={false}
            name="best_full_median_us"
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
      <div className="flex gap-4 mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-0.5 rounded"
            style={{ background: "#818cf8" }}
          />
          Avg eval timing / min
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-0.5 rounded"
            style={{ background: "#f97316" }}
          />
          Best full leader
        </span>
      </div>
    </div>
  );
}
