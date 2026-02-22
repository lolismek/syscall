import { spawn, execFile, type ChildProcess } from "child_process";
import { readFileSync, existsSync } from "fs";
import { resolve, dirname, join } from "path";
import { fileURLToPath } from "url";
import { createLogger } from "../utils/logger.js";

const log = createLogger("EvolutionManager");
const __dirname = dirname(fileURLToPath(import.meta.url));
const EVOLUTION_DIR = resolve(__dirname, "../../../evolution");

/** Active SSH tunnel process (shared across runs) */
let tunnelProcess: ChildProcess | null = null;

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

/** Read tunnel / eval worker config from env (mirrors run_medium_20m.sh variables) */
function getTunnelConfig(env: Record<string, string>) {
  const remoteEvalUrl = env.KERNELSWARM_REMOTE_EVAL_URL ?? "http://127.0.0.1:18080";
  const primaryUrl = remoteEvalUrl.split(",")[0];
  const tunnelMode = env.KERNELSWARM_TUNNEL_MODE ?? "ssh";
  const tunnelTarget = env.KERNELSWARM_TUNNEL_TARGET ?? "kernel-swarm-eval-new-2";
  const tunnelRemoteHost = env.KERNELSWARM_TUNNEL_REMOTE_HOST ?? "127.0.0.1";
  const tunnelRemotePort = env.KERNELSWARM_TUNNEL_REMOTE_PORT ?? "8080";
  const autoTunnel = env.KERNELSWARM_AUTO_TUNNEL !== "0";
  const sshOpts = (env.KERNELSWARM_TUNNEL_SSH_OPTS ?? "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null").split(" ");

  // Parse local port from the eval URL
  const urlNoScheme = primaryUrl.replace(/^https?:\/\//, "");
  const hostPort = urlNoScheme.split("/")[0];
  const localPort = hostPort.split(":")[1] ?? "18080";

  // Derive remote user for KB repo path
  let remoteUser = "shadeform";
  if (tunnelMode === "ssh" && tunnelTarget.includes("@")) {
    remoteUser = tunnelTarget.split("@")[0];
  }

  return { remoteEvalUrl, primaryUrl, tunnelMode, tunnelTarget, tunnelRemoteHost, tunnelRemotePort, autoTunnel, sshOpts, localPort, remoteUser };
}

/** Check if the remote eval worker is reachable via its health endpoint */
async function checkEvalHealth(healthUrl: string, timeoutMs = 3000): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const resp = await fetch(healthUrl, { signal: ctrl.signal });
    clearTimeout(timer);
    return resp.ok;
  } catch {
    return false;
  }
}

/**
 * Restart the remote eval worker via SSH (mirrors restart_remote_eval_worker in run_medium_20m.sh).
 * SSH-es to the remote machine, git pulls, kills old tmux eval session, starts a fresh one.
 */
async function restartRemoteEvalWorker(env: Record<string, string>): Promise<void> {
  const cfg = getTunnelConfig(env);
  if (cfg.tunnelMode !== "ssh" || !cfg.tunnelTarget.includes("@")) {
    log.info("Skipping eval worker restart (not SSH mode or no user@host target)");
    return;
  }

  log.info(`Restarting eval worker on ${cfg.tunnelTarget}...`);

  const remoteScript = `
set -eu
PORT="$1"
USER_HOME="/home/$2"
REPO="$USER_HOME/syscall/evolution"

cd "$REPO"
git pull --ff-only origin "$(git branch --show-current)" 2>&1 || true

# Kill existing eval worker tmux session (if any).
tmux kill-session -t eval 2>/dev/null || true
sleep 1

# Start fresh eval worker in tmux.
tmux new-session -d -s eval \
  "cd $REPO && PYTHONPATH=src .venv/bin/python -m kernelswarm serve-eval-worker --host 0.0.0.0 --port $PORT 2>&1 | tee /tmp/eval-worker.log"

# Wait for it to become healthy.
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "Eval worker restarted and healthy on port $PORT."
    exit 0
  fi
  sleep 1
done

echo "WARNING: eval worker not healthy after 10s" >&2
exit 1
`;

  return new Promise<void>((resolve) => {
    const sshArgs = [
      ...cfg.sshOpts,
      "-o", "ConnectTimeout=10",
      cfg.tunnelTarget,
      "bash", "-s", "--", cfg.tunnelRemotePort, cfg.remoteUser,
    ];

    const proc = execFile("ssh", sshArgs, { timeout: 30000 }, (err, stdout, stderr) => {
      if (stdout?.trim()) log.info(`[eval-restart] ${stdout.trim()}`);
      if (stderr?.trim()) log.warn(`[eval-restart] ${stderr.trim()}`);
      if (err) {
        log.warn(`Failed to restart remote eval worker: ${err.message} — continuing anyway`);
      }
      resolve();
    });
    proc.stdin?.write(remoteScript);
    proc.stdin?.end();
  });
}

/**
 * Ensure the remote eval endpoint is reachable. If not and auto-tunnel is enabled,
 * start an SSH tunnel (mirrors ensure_remote_eval_ready in run_medium_20m.sh).
 */
async function ensureRemoteEvalReady(env: Record<string, string>): Promise<void> {
  const cfg = getTunnelConfig(env);
  if (!cfg.remoteEvalUrl) return;

  const healthUrl = `${cfg.primaryUrl.replace(/\/$/, "")}/healthz`;

  if (await checkEvalHealth(healthUrl)) {
    log.info("Remote eval worker is healthy");
    return;
  }

  if (!cfg.autoTunnel) {
    log.warn(`Remote eval is unreachable at ${healthUrl} and auto-tunnel is disabled`);
    return;
  }

  // Only auto-tunnel for localhost URLs
  const urlNoScheme = cfg.primaryUrl.replace(/^https?:\/\//, "");
  const host = urlNoScheme.split(":")[0].split("/")[0];
  if (host !== "127.0.0.1" && host !== "localhost") {
    log.warn(`Auto tunnel only supports localhost remote-eval URLs (got ${host})`);
    return;
  }

  // Kill any previous tunnel
  if (tunnelProcess && !tunnelProcess.killed) {
    tunnelProcess.kill("SIGTERM");
    tunnelProcess = null;
  }

  log.info(`Remote eval unreachable, starting SSH tunnel: localhost:${cfg.localPort} -> ${cfg.tunnelTarget}:${cfg.tunnelRemotePort}`);

  tunnelProcess = spawn("ssh", [
    ...cfg.sshOpts,
    "-N",
    "-L", `${cfg.localPort}:${cfg.tunnelRemoteHost}:${cfg.tunnelRemotePort}`,
    cfg.tunnelTarget,
  ], {
    stdio: ["ignore", "pipe", "pipe"],
  });

  tunnelProcess.stderr?.on("data", (data: Buffer) => {
    const msg = data.toString().trim();
    if (msg) log.warn(`[ssh-tunnel] ${msg}`);
  });

  tunnelProcess.on("exit", (code) => {
    log.info(`SSH tunnel exited with code ${code}`);
    tunnelProcess = null;
  });

  // Wait up to 10s for the tunnel to become healthy
  for (let i = 0; i < 10; i++) {
    if (tunnelProcess?.killed || tunnelProcess?.exitCode !== null) {
      log.warn("SSH tunnel process exited early");
      return;
    }
    if (await checkEvalHealth(healthUrl)) {
      log.info(`SSH tunnel established (pid=${tunnelProcess?.pid})`);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  log.warn(`SSH tunnel started but remote eval health still failing at ${healthUrl}`);
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

export async function startEvolutionRun(prompt: string): Promise<{ evolutionRunId: string; dashboardPort: number }> {
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

  const childEnv = loadEvolutionEnv();

  // Pre-flight: restart remote eval worker & ensure tunnel is ready
  // (mirrors restart_remote_eval_worker + ensure_remote_eval_ready in run_medium_20m.sh)
  try {
    await restartRemoteEvalWorker(childEnv);
    await ensureRemoteEvalReady(childEnv);
  } catch (err) {
    log.warn(`Pre-flight eval worker setup had issues: ${err} — continuing anyway`);
  }

  // Spawn the search process
  // Using softmax YAML problem with remote GPU eval worker (L40S via SSH tunnel)
  const searchArgs = [
    "run", "python", "-m", "kernelswarm", "run-swarm-search",
    "--yaml-problem-path", "problems/softmax.yaml",
    "--remote-eval-url", "http://127.0.0.1:18080",
    "--nemotron-provider", "deepinfra",
    "--nemotron-model", "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "--generators", "8",
    "--max-iterations", "30",
    "--max-minutes", "10",
    "--workspace", workspacePath,
  ];

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

  // Clean up SSH tunnel
  if (tunnelProcess && !tunnelProcess.killed) {
    log.info("Cleaning up SSH tunnel");
    tunnelProcess.kill("SIGTERM");
    tunnelProcess = null;
  }
}
