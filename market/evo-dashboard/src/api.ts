import type {
  Run,
  Overview,
  Timeseries,
  LeaderboardRow,
  LeaderSource,
  StateCounts,
} from "./types.ts";

async function fetchJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const data = await r.json();
  return data as T;
}

export async function fetchRuns(limit = 100): Promise<Run[]> {
  const res = await fetchJson<{ ok: boolean; runs: Run[] }>(
    `/evo/api/runs?limit=${limit}`,
  );
  return res.runs;
}

export async function fetchOverview(runId: string): Promise<Overview> {
  const res = await fetchJson<{ ok: boolean; overview: Overview }>(
    `/evo/api/runs/${runId}/overview`,
  );
  return res.overview;
}

export async function fetchTimeseries(runId: string): Promise<Timeseries> {
  const res = await fetchJson<{ ok: boolean; timeseries: Timeseries }>(
    `/evo/api/runs/${runId}/timeseries`,
  );
  return res.timeseries;
}

export async function fetchLeaderboard(
  runId: string,
  stage: "quick" | "full",
  limit = 15,
): Promise<LeaderboardRow[]> {
  const res = await fetchJson<{
    ok: boolean;
    stage: string;
    rows: LeaderboardRow[];
  }>(`/evo/api/runs/${runId}/leaderboard?stage=${stage}&limit=${limit}`);
  return res.rows;
}

export async function fetchLeaderSource(
  runId: string,
  stage: "quick" | "full" = "full",
): Promise<LeaderSource> {
  const res = await fetchJson<{ ok: boolean; leader: LeaderSource }>(
    `/evo/api/runs/${runId}/leader-source?stage=${stage}`,
  );
  return res.leader;
}

export async function fetchStates(runId: string): Promise<StateCounts> {
  const res = await fetchJson<{ ok: boolean; states: StateCounts }>(
    `/evo/api/runs/${runId}/states`,
  );
  return res.states;
}
