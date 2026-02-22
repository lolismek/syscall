import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_PATH = resolve(__dirname, "../../data/evolution-runs.json");

interface EvolutionRun {
  id: string;
  name: string;
  description: string;
  problem_id: string;
  status: string;
  created_at: string;
  candidate_count: number;
  latest_iteration: number;
  total_tokens: number;
  best_full_median_us: number | null;
  best_quick_median_us: number | null;
  state_counts: Record<string, number>;
  best: {
    quick: { candidate_id: string; scalar_fitness: number; raw_score: unknown } | null;
    full: { candidate_id: string; scalar_fitness: number; raw_score: unknown } | null;
  };
  timeseries: {
    global: Array<Record<string, unknown>>;
    islands: Record<string, Array<Record<string, unknown>>>;
  };
  leaderboard_quick: Array<{
    candidate_id: string;
    scalar_fitness: number;
    median_us: number | null;
    state: string | null;
  }>;
  leaderboard_full: Array<{
    candidate_id: string;
    scalar_fitness: number;
    median_us: number | null;
    state: string | null;
  }>;
}

interface EvolutionData {
  runs: EvolutionRun[];
}

let cached: EvolutionData | null = null;

export function getEvolutionData(): EvolutionData {
  if (cached) return cached;
  try {
    const raw = readFileSync(DATA_PATH, "utf-8");
    cached = JSON.parse(raw) as EvolutionData;
    return cached;
  } catch {
    return { runs: [] };
  }
}

export function getEvolutionRun(id: string): EvolutionRun | undefined {
  return getEvolutionData().runs.find((r) => r.id === id);
}
