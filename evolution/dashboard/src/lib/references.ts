export interface ReferenceLine {
  label: string;
  value: number;
  color: string;
}

interface ProblemRefs {
  latency: ReferenceLine[];
  fitness: ReferenceLine[];
}

const latencyByProblem: Record<
  string,
  { baseline: number; good: number; sota_est: number }
> = {
  kernelbench_v1: { baseline: 30000, good: 10000, sota_est: 5000 },
  vector_add_v1: { baseline: 2000, good: 500, sota_est: 120 },
  reduction_v1: { baseline: 3000, good: 1000, sota_est: 250 },
  stencil2d_v1: { baseline: 2500, good: 900, sota_est: 280 },
};

export function getReferences(problemId: string): ProblemRefs | null {
  const ref = latencyByProblem[problemId.toLowerCase()];
  if (!ref) return null;

  const entries = [
    { label: "baseline", value: ref.baseline, color: "#6b7280" },
    { label: "good", value: ref.good, color: "#f59e0b" },
    { label: "sota est", value: ref.sota_est, color: "#22c55e" },
  ];

  return {
    latency: entries,
    fitness: entries.map((e) => ({
      ...e,
      value: 1_000_000 / e.value,
    })),
  };
}
