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
    return params.get("run") || params.get("run_id");
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
    <div className="min-h-screen" style={{ background: "#09090b" }}>
      {/* Ambient video background — same as market dashboard */}
      <div className="ambient-bg">
        <video
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          src="/public/ambient-wave.mp4"
        />
      </div>

      {/* Main content */}
      <div
        style={{
          maxWidth: 1400,
          margin: "0 auto",
          padding: "20px 24px",
          position: "relative",
          zIndex: 1,
        }}
      >
        <Header
          overview={overview}
          lastRefresh={lastRefresh}
        />

        <div className="space-y-4">
          {overview && timeseries && (
            <KpiBar overview={overview} timeseries={timeseries} />
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
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

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {timeseries && (
              <TokenChart data={timeseries.global} />
            )}
            {states && (
              <StateTable states={states} />
            )}
            {timeseries && (
              <div className="bg-surface-50 backdrop-blur-xl border border-surface-200 rounded-xl p-4 flex flex-col">
                <h3 className="text-sm font-semibold text-gray-400 mb-3">Island Coverage</h3>
                <IslandGrid islands={timeseries.islands} />
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Leaderboard title="Quick Leaderboard" rows={quickBoard} />
            <Leaderboard title="Full Leaderboard" rows={fullBoard} />
          </div>
        </div>
      </div>
    </div>
  );
}
