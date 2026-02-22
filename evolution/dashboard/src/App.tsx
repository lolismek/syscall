import { useState, useEffect, useCallback } from "react";
import {
  fetchRuns,
  fetchOverview,
  fetchTimeseries,
  fetchLeaderboard,
  fetchLeaderSource,
  fetchStates,
} from "./api.ts";
import type {
  Run,
  Overview,
  Timeseries,
  LeaderboardRow,
  LeaderSource,
  StateCounts,
} from "./types.ts";
import { usePolling } from "./hooks/usePolling.ts";
import Header from "./components/Header.tsx";
import KpiBar from "./components/KpiBar.tsx";
import FitnessChart from "./components/FitnessChart.tsx";
import LatencyChart from "./components/LatencyChart.tsx";
import TokenChart from "./components/TokenChart.tsx";
import Leaderboard from "./components/Leaderboard.tsx";
import IslandGrid from "./components/IslandGrid.tsx";
import LeaderCode from "./components/LeaderCode.tsx";
import StateTable from "./components/StateTable.tsx";

export default function App() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("run_id");
  });
  const [overview, setOverview] = useState<Overview | null>(null);
  const [timeseries, setTimeseries] = useState<Timeseries | null>(null);
  const [quickBoard, setQuickBoard] = useState<LeaderboardRow[]>([]);
  const [fullBoard, setFullBoard] = useState<LeaderboardRow[]>([]);
  const [leaderSource, setLeaderSource] = useState<LeaderSource | null>(null);
  const [states, setStates] = useState<StateCounts | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  useEffect(() => {
    fetchRuns().then((r) => {
      setRuns(r);
      if (!activeRunId && r.length > 0) {
        setActiveRunId(r[0].run_id);
      }
    });
  }, []);

  const refresh = useCallback(async () => {
    if (!activeRunId) return;
    try {
      const [ov, ts, qb, fb, ls, st] = await Promise.all([
        fetchOverview(activeRunId),
        fetchTimeseries(activeRunId),
        fetchLeaderboard(activeRunId, "quick", 15),
        fetchLeaderboard(activeRunId, "full", 15),
        fetchLeaderSource(activeRunId, "full"),
        fetchStates(activeRunId),
      ]);
      setOverview(ov);
      setTimeseries(ts);
      setQuickBoard(qb);
      setFullBoard(fb);
      setLeaderSource(ls);
      setStates(st);
      setLastRefresh(new Date());
    } catch (e) {
      console.error("refresh failed:", e);
    }
  }, [activeRunId]);

  usePolling(refresh, 2000);

  return (
    <div className="min-h-screen bg-[#0a0c10]">
      <Header
        runs={runs}
        activeRunId={activeRunId}
        onRunChange={setActiveRunId}
        overview={overview}
        lastRefresh={lastRefresh}
      />

      <main className="mx-auto max-w-[1600px] px-4 py-5 space-y-5">
        {overview && timeseries && (
          <KpiBar overview={overview} timeseries={timeseries} />
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {timeseries && overview && (
            <>
              <FitnessChart
                data={timeseries.global}
                problemId={overview.problem_id}
              />
              <LatencyChart
                data={timeseries.global}
                problemId={overview.problem_id}
              />
            </>
          )}
        </div>

        {leaderSource && leaderSource.candidate_id && (
          <LeaderCode leader={leaderSource} />
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {timeseries && (
            <TokenChart data={timeseries.global} />
          )}
          {states && (
            <StateTable states={states} />
          )}
          {timeseries && (
            <div className="bg-surface-50 border border-surface-200 rounded-xl p-4">
              <h3 className="text-sm font-semibold text-gray-400 mb-3">Island Coverage</h3>
              <IslandGrid islands={timeseries.islands} />
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <Leaderboard title="Quick Leaderboard" rows={quickBoard} />
          <Leaderboard title="Full Leaderboard" rows={fullBoard} />
        </div>
      </main>
    </div>
  );
}
