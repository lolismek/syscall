import express from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { McpServerFactory, SessionContext } from "./server.js";
import type { ProjectRegistry } from "../state/project-registry.js";
import type { GitHubClient } from "../git/github.js";
import { createProject } from "../orchestrator/create-project.js";
import { config } from "../utils/config.js";
import { createLogger } from "../utils/logger.js";
import { getNiaEvents } from "../knowledge/nia-client.js";

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
    const { idea, model, recruitingDurationSeconds, minAgents } = req.body as {
      idea?: string; model?: string; recruitingDurationSeconds?: number; minAgents?: number;
    };
    if (!idea || typeof idea !== "string") {
      res.status(400).json({ error: "Missing required field: idea (string)" });
      return;
    }

    if (model) {
      config.model = model;
    }

    try {
      const ctx = await createProject(idea, registry, githubClient, config.workspacePath, {
        recruitingDurationMs: recruitingDurationSeconds !== undefined ? recruitingDurationSeconds * 1000 : undefined,
        minAgents,
      });
      res.status(201).json({
        projectId: ctx.project.id,
        name: ctx.project.name,
        description: ctx.project.description,
        status: ctx.project.status,
        recruitingUntil: ctx.project.recruitingUntil,
        minAgents: ctx.project.minAgents,
        githubUrl: ctx.project.githubRepoUrl,
        taskCount: ctx.taskBoard.getAllTasks().length,
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
    niaEvents: getNiaEvents(),
    timestamp: new Date().toISOString(),
  };
}
