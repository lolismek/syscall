import { spawn, type ChildProcess } from "child_process";
import { readFileSync, existsSync } from "fs";
import { resolve, dirname, join } from "path";
import { fileURLToPath } from "url";
import { createLogger } from "../utils/logger.js";

const log = createLogger("EvolutionManager");
const __dirname = dirname(fileURLToPath(import.meta.url));
const EVOLUTION_DIR = resolve(__dirname, "../../../evolution");

/** Parse evolution/.env and merge with process.env for child processes */
function loadEvolutionEnv(): Record<string, string> {
  const env: Record<string, string> = { ...process.env as Record<string, string> };
  try {
    const dotenvPath = join(EVOLUTION_DIR, ".env");
    const content = readFileSync(dotenvPath, "utf-8");
    for (const raw of content.split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) continue;
      const eqIdx = line.indexOf("=");
      const key = line.slice(0, eqIdx).trim();
      let value = line.slice(eqIdx + 1).trim();
      if (!key) continue;
      // Strip surrounding quotes
      if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === "'" || value[0] === '"')) {
        value = value.slice(1, -1);
      }
      // setdefault: don't override existing env vars
      if (!(key in env)) {
        env[key] = value;
      }
    }
  } catch {
    log.warn("Could not read evolution/.env — child processes may lack required env vars");
  }
  return env;
}

interface LiveEvolutionRun {
  id: string;
  prompt: string;
  workspacePath: string;
  dashboardPort: number;
  searchProcess: ChildProcess | null;
  dashboardProcess: ChildProcess | null;
  status: "starting" | "running" | "stopped" | "failed";
  createdAt: string;
  error?: string;
}

const liveRuns = new Map<string, LiveEvolutionRun>();
let nextPort = 8100;

export function startEvolutionRun(prompt: string): { evolutionRunId: string; dashboardPort: number } {
  const id = `evo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const workspacePath = `.runs/${id}`;
  const port = nextPort++;

  const run: LiveEvolutionRun = {
    id,
    prompt,
    workspacePath,
    dashboardPort: port,
    searchProcess: null,
    dashboardProcess: null,
    status: "starting",
    createdAt: new Date().toISOString(),
  };

  liveRuns.set(id, run);

  // Spawn the search process
  // Using reduction_v1 (takes ~60-90s) instead of vector_add_v1 (too fast for live demo)
  const searchArgs = [
    "run", "python", "-m", "kernelswarm", "run-swarm-search",
    "--problem-id", "reduction_v1",
    "--backend", "python-sim",
    "--max-iterations", "300",
    "--workspace", workspacePath,
  ];

  const childEnv = loadEvolutionEnv();

  log.info(`Starting evolution search: uv ${searchArgs.join(" ")} (cwd=${EVOLUTION_DIR})`);

  try {
    const searchProc = spawn("uv", searchArgs, {
      cwd: EVOLUTION_DIR,
      stdio: ["ignore", "pipe", "pipe"],
      env: childEnv,
    });

    run.searchProcess = searchProc;

    searchProc.stdout?.on("data", (data: Buffer) => {
      const lines = data.toString().trim();
      if (lines) log.info(`[evo-search:${id}] ${lines}`);
    });

    searchProc.stderr?.on("data", (data: Buffer) => {
      const lines = data.toString().trim();
      if (lines) log.warn(`[evo-search:${id}] ${lines}`);
    });

    searchProc.on("error", (err) => {
      log.error(`Evolution search process error for ${id}: ${err.message}`);
      run.status = "failed";
      run.error = err.message;
    });

    searchProc.on("exit", (code) => {
      log.info(`Evolution search process exited for ${id} with code ${code}`);
      if (code !== 0 && run.status !== "stopped") {
        run.status = "failed";
      }
      // Don't set "stopped" here — the dashboard sidecar is still serving data
    });
  } catch (err) {
    log.error(`Failed to spawn evolution search for ${id}: ${err}`);
    run.status = "failed";
    run.error = String(err);
    return { evolutionRunId: id, dashboardPort: port };
  }

  // Wait for the SQLite DB to appear, then spawn the dashboard sidecar immediately.
  // The search process creates the DB within the first ~1s.
  const dbPath = join(EVOLUTION_DIR, workspacePath, "db", "runs.sqlite");
  let dbPollAttempts = 0;
  const maxDbPollAttempts = 60; // 30s max wait (500ms interval)

  const dbPollTimer = setInterval(() => {
    dbPollAttempts++;
    if (run.status === "stopped" || run.status === "failed") {
      clearInterval(dbPollTimer);
      return;
    }
    if (!existsSync(dbPath)) {
      if (dbPollAttempts >= maxDbPollAttempts) {
        clearInterval(dbPollTimer);
        log.warn(`DB never appeared for ${id} after ${maxDbPollAttempts * 0.5}s — skipping dashboard sidecar`);
      }
      return;
    }

    clearInterval(dbPollTimer);
    log.info(`DB appeared for ${id} after ~${(dbPollAttempts * 0.5).toFixed(1)}s — starting dashboard sidecar`);

    const dashArgs = [
      "run", "python", "-m", "kernelswarm", "serve-dashboard",
      "--workspace", workspacePath,
      "--host", "127.0.0.1",
      "--port", String(port),
    ];

    log.info(`Starting evolution dashboard sidecar: uv ${dashArgs.join(" ")} (port=${port})`);

    try {
      const dashProc = spawn("uv", dashArgs, {
        cwd: EVOLUTION_DIR,
        stdio: ["ignore", "pipe", "pipe"],
        env: childEnv,
      });

      run.dashboardProcess = dashProc;
      run.status = "running";

      dashProc.stdout?.on("data", (data: Buffer) => {
        const lines = data.toString().trim();
        if (lines) log.info(`[evo-dash:${id}] ${lines}`);
      });

      dashProc.stderr?.on("data", (data: Buffer) => {
        const lines = data.toString().trim();
        if (lines) log.warn(`[evo-dash:${id}] ${lines}`);
      });

      dashProc.on("error", (err) => {
        log.error(`Evolution dashboard process error for ${id}: ${err.message}`);
      });

      dashProc.on("exit", (code) => {
        log.info(`Evolution dashboard process exited for ${id} with code ${code}`);
      });
    } catch (err) {
      log.error(`Failed to spawn evolution dashboard for ${id}: ${err}`);
    }
  }, 500);

  return { evolutionRunId: id, dashboardPort: port };
}

export function getLiveRun(id: string): LiveEvolutionRun | undefined {
  return liveRuns.get(id);
}

export function listLiveRuns(): LiveEvolutionRun[] {
  return Array.from(liveRuns.values());
}

export function stopEvolutionRun(id: string): boolean {
  const run = liveRuns.get(id);
  if (!run) return false;

  run.status = "stopped";

  if (run.searchProcess && !run.searchProcess.killed) {
    run.searchProcess.kill("SIGTERM");
  }
  if (run.dashboardProcess && !run.dashboardProcess.killed) {
    run.dashboardProcess.kill("SIGTERM");
  }

  return true;
}

export function cleanupAll(): void {
  for (const [id, run] of liveRuns) {
    log.info(`Cleaning up evolution run ${id}`);
    if (run.searchProcess && !run.searchProcess.killed) {
      run.searchProcess.kill("SIGTERM");
    }
    if (run.dashboardProcess && !run.dashboardProcess.killed) {
      run.dashboardProcess.kill("SIGTERM");
    }
  }
  liveRuns.clear();
}
