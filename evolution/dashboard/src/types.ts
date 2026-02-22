export interface Run {
  run_id: string;
  problem_id: string;
  status: string;
  created_at: string;
  summary: Record<string, unknown>;
}

export interface Overview {
  run_id: string;
  problem_id: string;
  status: string;
  created_at: string;
  manifest: Record<string, unknown>;
  config: Record<string, unknown>;
  summary: Record<string, unknown>;
  state_counts: Record<string, number>;
  best: {
    quick: ScoreSummary | null;
    full: ScoreSummary | null;
  };
  latest_iteration: {
    iteration: number;
    global_best_candidate_id: string | null;
    global_best_fitness: number | null;
    total_tokens: number;
  } | null;
}

export interface ScoreSummary {
  candidate_id: string;
  scalar_fitness: number;
  raw_score: Record<string, unknown>;
}

export interface TimeseriesPoint {
  iteration: number;
  created_at: string | null;
  active_island_id: string;
  active_candidate_id: string | null;
  quick_fitness: number | null;
  full_fitness: number | null;
  quick_median_us: number | null;
  full_median_us: number | null;
  global_best_candidate_id: string | null;
  global_best_fitness: number | null;
  global_best_quick_median_us: number | null;
  global_best_full_median_us: number | null;
  global_best_median_us: number | null;
  total_tokens: number;
  best_quick_fitness: number | null;
  best_full_fitness: number | null;
  best_quick_median_us: number | null;
  best_full_median_us: number | null;
  best_representative_fitness: number | null;
  best_representative_median_us: number | null;
}

export interface IslandPoint {
  iteration: number;
  top_fitness: number | null;
  coverage_ratio: number;
  occupied_bins: number;
  accepted_updates: number;
}

export interface Timeseries {
  run_id: string;
  global: TimeseriesPoint[];
  islands: Record<string, IslandPoint[]>;
}

export interface LeaderboardRow {
  candidate_id: string;
  scalar_fitness: number;
  created_at: string;
  state: string | null;
  raw_score: Record<string, unknown>;
}

export interface LeaderSource {
  candidate_id: string | null;
  fitness: number | null;
  stage: string;
  files: { path: string; content: string }[];
  hypothesis: string;
  origin: Record<string, unknown>;
}

export interface StateCounts {
  run_id: string;
  state_counts: Record<string, number>;
}
