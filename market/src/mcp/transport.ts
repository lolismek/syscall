import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { McpServerFactory, SessionContext } from "./server.js";
import type { ProjectRegistry } from "../state/project-registry.js";
import type { GitHubClient } from "../git/github.js";
import { initProject, planAndFinalize } from "../orchestrator/create-project.js";
import { config } from "../utils/config.js";
import { createLogger } from "../utils/logger.js";
import { getNiaEvents } from "../knowledge/nia-client.js";
import { getDashboardHtml } from "../dashboard/html.js";
import { getEvolutionData, getEvolutionRun } from "../state/evolution-data.js";
import { startEvolutionRun, getLiveRun, listLiveRuns } from "../state/evolution-manager.js";

const log = createLogger("Transport");

export function createTransport(
  serverFactory: McpServerFactory,
  registry: ProjectRegistry,
  githubClient: GitHubClient | null,
): express.Express {
  const app = express();
  const jsonParser = express.json();

  // Auth middleware for /mcp routes
  if (config.agentApiKey) {
    app.use("/mcp", (req, res, next) => {
      const auth = req.headers.authorization;
      if (!auth || auth !== `Bearer ${config.agentApiKey}`) {
        res.status(401).json({ error: "Unauthorized. Provide Authorization: Bearer <AGENT_API_KEY> header." });
        return;
      }
      next();
    });
  }

  // Map of sessionId -> transport for multi-session support
  const transports = new Map<string, StreamableHTTPServerTransport>();

  app.post("/mcp", async (req, res) => {
    try {
      const sessionId = req.headers["mcp-session-id"] as string | undefined;

      if (sessionId && transports.has(sessionId)) {
        const transport = transports.get(sessionId)!;
        await transport.handleRequest(req, res);
        return;
      }

      // New session — create a fresh server + transport pair with mutable session context
      const sessionCtx: SessionContext = { projectId: null, agentId: null };

      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => `session-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      });

      transport.onclose = () => {
        const sid = (transport as unknown as { sessionId?: string }).sessionId;
        if (sid) transports.delete(sid);

        // Agent disconnected — return their active task to the pool
        if (sessionCtx.agentId && sessionCtx.projectId) {
          const ctx = registry.get(sessionCtx.projectId);
          if (ctx) {
            const agent = ctx.taskBoard.getAgent(sessionCtx.agentId);
            if (agent?.currentTaskId) {
              const task = ctx.taskBoard.getTask(agent.currentTaskId);
              if (task && !["accepted", "submitted"].includes(task.status)) {
                ctx.taskBoard.reassignTask(agent.currentTaskId);
                log.warn(`Agent ${sessionCtx.agentId} disconnected — task ${agent.currentTaskId} returned to pool`);
              }
            }
          }
        }
      };

      const server = serverFactory(sessionCtx);
      await server.connect(transport);
      await transport.handleRequest(req, res);

      const newSessionId = res.getHeader("mcp-session-id") as string | undefined;
      if (newSessionId) {
        transports.set(newSessionId, transport);
        log.info(`New MCP session: ${newSessionId}`);
      }
    } catch (err) {
      log.error(`MCP request error: ${err}`);
      if (!res.headersSent) {
        res.status(500).json({ error: "Internal server error" });
      }
    }
  });

  app.get("/mcp", async (req, res) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    if (sessionId && transports.has(sessionId)) {
      const transport = transports.get(sessionId)!;
      await transport.handleRequest(req, res);
    } else {
      res.status(400).json({ error: "No session. Send an initialize request via POST first." });
    }
  });

  app.delete("/mcp", async (req, res) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    if (sessionId && transports.has(sessionId)) {
      const transport = transports.get(sessionId)!;
      await transport.handleRequest(req, res);
      transports.delete(sessionId);
    } else {
      res.status(400).json({ error: "No session found" });
    }
  });

  // CORS for /api routes
  app.use("/api", (_req, res, next) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    if (_req.method === "OPTIONS") { res.sendStatus(204); return; }
    next();
  });

  // --- POST /api/projects --- Create a new project
  app.post("/api/projects", jsonParser, async (req, res) => {
    const { idea, model, recruitingDurationSeconds, minAgents, useEvolution } = req.body as {
      idea?: string; model?: string; recruitingDurationSeconds?: number; minAgents?: number; useEvolution?: boolean;
    };
    if (!idea || typeof idea !== "string") {
      res.status(400).json({ error: "Missing required field: idea (string)" });
      return;
    }

    // Evolution mode — spawn evolution processes instead of normal project
    if (useEvolution) {
      try {
        const { evolutionRunId, dashboardPort } = await startEvolutionRun(idea);
        log.info(`Started evolution run ${evolutionRunId} on port ${dashboardPort} for prompt: ${idea.slice(0, 80)}`);
        res.status(201).json({ evolutionRunId, dashboardPort });
      } catch (err) {
        log.error(`Failed to start evolution run: ${err}`);
        res.status(500).json({ error: `Failed to start evolution: ${err}` });
      }
      return;
    }

    if (model) {
      config.model = model;
    }

    try {
      // Phase 1: Create project shell immediately so the dashboard can show it
      const ctx = await initProject(idea, registry, config.workspacePath);

      // Return immediately — project is visible on dashboard in "planning" status
      res.status(201).json({
        projectId: ctx.project.id,
        name: ctx.project.name,
        description: ctx.project.description,
        status: ctx.project.status,
      });

      // Phase 2: Plan in background — dashboard polls and sees progress
      planAndFinalize(ctx, githubClient, config.workspacePath, {
        recruitingDurationMs: recruitingDurationSeconds !== undefined ? recruitingDurationSeconds * 1000 : undefined,
        minAgents,
      }).catch((err) => {
        log.error(`Background planning failed for ${ctx.project.id}: ${err}`);
      });
    } catch (err) {
      log.error(`Failed to create project: ${err}`);
      res.status(500).json({ error: `Failed to create project: ${err}` });
    }
  });

  // --- GET /api/projects --- List all projects
  app.get("/api/projects", (_req, res) => {
    const projects = registry.list().map((ctx) => {
      const tasks = ctx.taskBoard.getAllTasks();
      const accepted = tasks.filter((t) => t.status === "accepted").length;
      const inProgress = tasks.filter((t) => ["assigned", "in_progress", "submitted"].includes(t.status)).length;
      return {
        id: ctx.project.id,
        name: ctx.project.name,
        description: ctx.project.description,
        status: ctx.project.status,
        githubUrl: ctx.project.githubRepoUrl,
        taskCount: tasks.length,
        accepted,
        inProgress,
        createdAt: ctx.project.createdAt,
      };
    });
    res.json({ projects });
  });

  // --- POST /api/projects/:id/stop --- Stop a project permanently
  app.post("/api/projects/:id/stop", jsonParser, (req, res) => {
    const ctx = registry.get(req.params.id);
    if (!ctx) {
      res.status(404).json({ error: `Project not found: ${req.params.id}` });
      return;
    }
    if (ctx.project.status === "stopped") {
      res.json({ message: "Project already stopped", projectId: ctx.project.id });
      return;
    }
    ctx.project.status = "stopped";
    ctx.project.recruitingUntil = null;
    ctx.taskBoard.setProject(ctx.project);
    log.info(`Project stopped: ${ctx.project.id} — ${ctx.project.name}`);
    res.json({ message: "Project stopped", projectId: ctx.project.id });
  });

  // --- DELETE /api/projects/:id --- Delete a project and all its data
  app.delete("/api/projects/:id", async (req, res) => {
    const ctx = registry.get(req.params.id);
    if (!ctx) {
      res.status(404).json({ error: `Project not found: ${req.params.id}` });
      return;
    }
    try {
      await registry.delete(req.params.id, config.workspacePath);
      log.info(`Project deleted: ${req.params.id}`);
      res.json({ message: "Project deleted", projectId: req.params.id });
    } catch (err) {
      log.error(`Failed to delete project ${req.params.id}: ${err}`);
      res.status(500).json({ error: `Failed to delete project: ${err}` });
    }
  });

  // --- GET /api/status --- Project-specific or all-projects status
  app.get("/api/status", (req, res) => {
    const projectId = req.query.project_id as string | undefined;

    if (projectId) {
      // Detailed status for a specific project
      const ctx = registry.get(projectId);
      if (!ctx) {
        res.status(404).json({ error: `Project not found: ${projectId}` });
        return;
      }
      res.json(buildProjectStatus(ctx));
    } else {
      // If only one project exists, return its status directly (backwards compat)
      const all = registry.list();
      if (all.length === 1) {
        res.json(buildProjectStatus(all[0]));
      } else if (all.length === 0) {
        res.json({ project: null, tasks: [], agents: [], progress: { total: 0, accepted: 0, inProgress: 0, pending: 0, failed: 0 }, timestamp: new Date().toISOString() });
      } else {
        // Multiple projects — return summary list
        const projects = all.map((ctx) => {
          const tasks = ctx.taskBoard.getAllTasks();
          return {
            id: ctx.project.id,
            name: ctx.project.name,
            status: ctx.project.status,
            total: tasks.length,
            accepted: tasks.filter((t) => t.status === "accepted").length,
          };
        });
        res.json({ projects, timestamp: new Date().toISOString() });
      }
    }
  });

  // --- GET /api/evolution-runs --- List evolution runs (card data, static + live)
  app.get("/api/evolution-runs", (_req, res) => {
    const data = getEvolutionData();
    const cards = data.runs.map((r) => ({
      id: r.id,
      name: r.name,
      description: r.description,
      problem_id: r.problem_id,
      status: r.status,
      candidate_count: r.candidate_count,
      latest_iteration: r.latest_iteration,
      best_quick_fitness: r.best?.quick?.scalar_fitness ?? null,
      best_full_fitness: r.best?.full?.scalar_fitness ?? null,
      live: false,
    }));

    // Merge live evolution runs
    for (const run of listLiveRuns()) {
      cards.unshift({
        id: run.id,
        name: `Evolution: ${run.prompt.slice(0, 50)}`,
        description: run.prompt,
        problem_id: "reduction_v1",
        status: run.status,
        candidate_count: 0,
        latest_iteration: 0,
        best_quick_fitness: null,
        best_full_fitness: null,
        live: true,
      });
    }

    res.json({ runs: cards });
  });

  // --- GET /api/evolution-runs/:id --- Full detail for one evolution run
  app.get("/api/evolution-runs/:id", (req, res) => {
    const run = getEvolutionRun(req.params.id);
    if (!run) {
      res.status(404).json({ error: `Evolution run not found: ${req.params.id}` });
      return;
    }
    res.json(run);
  });

  // --- GET /api/evolution-live/:evoId/* --- Proxy to live sidecar dashboard
  app.get("/api/evolution-live/:evoId/*", async (req, res) => {
    const run = getLiveRun(req.params.evoId);
    if (!run) {
      res.status(404).json({ error: `Live evolution run not found: ${req.params.evoId}` });
      return;
    }
    if (run.status === "starting" || run.status === "failed") {
      res.status(503).json({ error: `Evolution run ${req.params.evoId} is ${run.status} (sidecar not ready)` });
      return;
    }

    // Extract the suffix after /api/evolution-live/:evoId/
    const suffix = (req.params as unknown as Record<string, string>)[0] || "";
    const targetUrl = `http://127.0.0.1:${run.dashboardPort}/api/${suffix}`;

    try {
      const proxyRes = await fetch(targetUrl);
      const data = await proxyRes.text();
      res.status(proxyRes.status)
        .setHeader("Content-Type", proxyRes.headers.get("content-type") || "application/json")
        .send(data);
    } catch (err) {
      res.status(502).json({ error: `Failed to proxy to evolution sidecar: ${err}` });
    }
  });

  // ===== Evolution Dashboard Sub-App API (/evo/api/*) =====
  // These routes serve the React evolution dashboard at /evo/
  // They merge static runs (from JSON) and live runs (from sidecar proxy)

  // Cache: live run sidecar internal run_id mapping
  const liveRunIdCache = new Map<string, string>();

  app.use("/evo/api", (_req, res, next) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    if (_req.method === "OPTIONS") { res.sendStatus(204); return; }
    next();
  });

  // GET /evo/api/runs — merge static + live runs
  app.get("/evo/api/runs", async (_req, res) => {
    const data = getEvolutionData();
    const runs: Record<string, unknown>[] = data.runs.map((r) => ({
      run_id: r.id,
      problem_id: r.problem_id,
      status: r.status,
      created_at: r.created_at,
      summary: {},
    }));

    // Add live runs
    for (const live of listLiveRuns()) {
      if (live.status === "running") {
        // Try to discover internal run_id from sidecar
        try {
          const sidecarRes = await fetch(`http://127.0.0.1:${live.dashboardPort}/api/runs`);
          if (sidecarRes.ok) {
            const sidecarData = await sidecarRes.json() as { ok: boolean; runs: Array<{ run_id: string; problem_id: string; status: string; created_at: string }> };
            for (const sr of sidecarData.runs || []) {
              liveRunIdCache.set(live.id, sr.run_id);
              runs.unshift({
                run_id: live.id,
                problem_id: sr.problem_id,
                status: sr.status,
                created_at: sr.created_at || live.createdAt,
                summary: {},
              });
            }
          }
        } catch {
          // Sidecar not ready
          runs.unshift({
            run_id: live.id,
            problem_id: "starting...",
            status: live.status,
            created_at: live.createdAt,
            summary: {},
          });
        }
      }
    }

    res.json({ ok: true, runs });
  });

  // Helper: route to either static data or live sidecar proxy
  async function handleEvoRunEndpoint(
    runId: string,
    endpoint: string,
    query: Record<string, string>,
    res: express.Response,
  ) {
    // Check live runs first
    const liveRun = getLiveRun(runId);
    if (liveRun) {
      if (liveRun.status !== "running") {
        res.status(503).json({ error: `Live run ${runId} is ${liveRun.status}` });
        return;
      }
      const internalId = liveRunIdCache.get(runId);
      if (!internalId) {
        // Try to discover
        try {
          const sidecarRes = await fetch(`http://127.0.0.1:${liveRun.dashboardPort}/api/runs`);
          const sidecarData = await sidecarRes.json() as { runs: Array<{ run_id: string }> };
          if (sidecarData.runs?.[0]?.run_id) {
            liveRunIdCache.set(runId, sidecarData.runs[0].run_id);
          }
        } catch {
          res.status(503).json({ error: "Sidecar not ready" });
          return;
        }
      }
      const actualId = liveRunIdCache.get(runId);
      if (!actualId) {
        res.status(503).json({ error: "Could not discover sidecar run_id" });
        return;
      }
      // Proxy to sidecar
      const qs = new URLSearchParams(query).toString();
      const targetUrl = `http://127.0.0.1:${liveRun.dashboardPort}/api/runs/${actualId}/${endpoint}${qs ? "?" + qs : ""}`;
      try {
        const proxyRes = await fetch(targetUrl);
        const data = await proxyRes.text();
        res.status(proxyRes.status)
          .setHeader("Content-Type", proxyRes.headers.get("content-type") || "application/json")
          .send(data);
      } catch (err) {
        res.status(502).json({ error: `Failed to proxy: ${err}` });
      }
      return;
    }

    // Static run
    const run = getEvolutionRun(runId);
    if (!run) {
      res.status(404).json({ error: `Run not found: ${runId}` });
      return;
    }

    switch (endpoint) {
      case "overview":
        res.json({
          ok: true,
          overview: {
            run_id: run.id,
            problem_id: run.problem_id,
            status: run.status,
            created_at: run.created_at,
            manifest: {},
            config: {},
            summary: {},
            state_counts: run.state_counts,
            best: run.best,
            latest_iteration: {
              iteration: run.latest_iteration,
              global_best_candidate_id: run.best?.full?.candidate_id ?? run.best?.quick?.candidate_id ?? null,
              global_best_fitness: run.best?.full?.scalar_fitness ?? run.best?.quick?.scalar_fitness ?? null,
              total_tokens: run.total_tokens,
            },
          },
        });
        break;

      case "timeseries":
        res.json({
          ok: true,
          timeseries: {
            run_id: run.id,
            global: run.timeseries?.global ?? [],
            islands: run.timeseries?.islands ?? {},
          },
        });
        break;

      case "states":
        res.json({
          ok: true,
          states: {
            run_id: run.id,
            state_counts: run.state_counts,
          },
        });
        break;

      case "leaderboard": {
        const stage = query.stage === "full" ? "full" : "quick";
        const limit = Math.min(parseInt(query.limit || "15", 10), 100);
        const source = stage === "full" ? run.leaderboard_full : run.leaderboard_quick;
        const rows = (source || []).slice(0, limit).map((e) => ({
          candidate_id: e.candidate_id,
          scalar_fitness: e.scalar_fitness,
          state: e.state,
          raw_score: e.raw_score ?? { median_us: e.median_us },
          created_at: run.created_at,
        }));
        res.json({ ok: true, stage, rows });
        break;
      }

      case "leader-source":
        res.json({ ok: true, leader: null });
        break;

      default:
        res.status(404).json({ error: `Unknown endpoint: ${endpoint}` });
    }
  }

  app.get("/evo/api/runs/:id/overview", (req, res) => {
    handleEvoRunEndpoint(req.params.id, "overview", {}, res);
  });
  app.get("/evo/api/runs/:id/timeseries", (req, res) => {
    handleEvoRunEndpoint(req.params.id, "timeseries", {}, res);
  });
  app.get("/evo/api/runs/:id/states", (req, res) => {
    handleEvoRunEndpoint(req.params.id, "states", {}, res);
  });
  app.get("/evo/api/runs/:id/leaderboard", (req, res) => {
    handleEvoRunEndpoint(req.params.id, "leaderboard", req.query as Record<string, string>, res);
  });
  app.get("/evo/api/runs/:id/leader-source", (req, res) => {
    handleEvoRunEndpoint(req.params.id, "leader-source", req.query as Record<string, string>, res);
  });

  // --- Static assets (public/) ---
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  app.use("/public", express.static(path.resolve(__dirname, "../../public")));

  // --- Serve evolution dashboard static files at /evo/ ---
  app.use("/evo", express.static(path.resolve(__dirname, "../../public/evo")));
  // SPA fallback: serve index.html for all unmatched /evo/* paths
  app.get("/evo/*", (_req, res) => {
    res.sendFile(path.resolve(__dirname, "../../public/evo/index.html"));
  });

  // --- GET / --- Serve the dashboard
  app.get("/", (_req, res) => {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.send(getDashboardHtml());
  });

  app.get("/health", (_req, res) => {
    res.json({ status: "ok", projects: registry.list().length });
  });

  // Catch OAuth endpoints — we don't use auth
  app.post("/register", jsonParser, (_req, res) => {
    res.status(404).json({ error: "OAuth not supported. Connect directly to /mcp." });
  });
  app.all("/.well-known/oauth-authorization-server", (_req, res) => {
    res.status(404).json({ error: "OAuth not supported." });
  });

  return app;
}

function buildProjectStatus(ctx: import("../state/project-registry.js").ProjectContext) {
  const { project, taskBoard } = ctx;
  const tasks = taskBoard.getAllTasks();
  const agents = taskBoard.getAllAgents();

  const progress = {
    total: tasks.length,
    accepted: tasks.filter((t) => t.status === "accepted").length,
    inProgress: tasks.filter((t) => ["assigned", "in_progress", "submitted"].includes(t.status)).length,
    pending: tasks.filter((t) => t.status === "pending").length,
    failed: tasks.filter((t) => ["rejected", "failed"].includes(t.status)).length,
  };

  const projectInfo: Record<string, unknown> = {
    id: project.id,
    name: project.name,
    description: project.description,
    status: project.status,
    githubUrl: project.githubRepoUrl,
  };

  if (project.status === "recruiting" && project.recruitingUntil) {
    const remainingMs = Math.max(0, new Date(project.recruitingUntil).getTime() - Date.now());
    projectInfo.recruitingUntil = project.recruitingUntil;
    projectInfo.recruitingRemainingSeconds = Math.ceil(remainingMs / 1000);
    projectInfo.minAgents = project.minAgents;
    projectInfo.connectedAgents = agents.length;
  }

  return {
    project: projectInfo,
    tasks: tasks.map((t) => ({
      id: t.id,
      title: t.spec.title,
      status: t.status,
      assignedTo: t.assignedTo,
      branch: t.branch,
      dependencies: t.spec.dependencies,
      filePaths: t.spec.filePaths,
    })),
    agents: agents.map((a) => ({
      id: a.id,
      name: a.name,
      capabilities: a.capabilities,
      joinedAt: a.joinedAt,
      currentTaskId: a.currentTaskId,
    })),
    progress,
    niaEvents: getNiaEvents(project.id),
    timestamp: new Date().toISOString(),
  };
}
