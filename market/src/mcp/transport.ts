import express from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { McpServerFactory } from "./server.js";
import type { TaskBoard } from "../state/task-board.js";
import type { ProjectStore } from "../state/project-store.js";
import { config } from "../utils/config.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("Transport");

export function createTransport(
  serverFactory: McpServerFactory,
  taskBoard: TaskBoard,
  projectStore: ProjectStore,
): express.Express {
  const app = express();
  // IMPORTANT: Do NOT use express.json() globally — the MCP StreamableHTTP
  // transport needs the raw body to parse JSON-RPC itself. Only parse JSON
  // on non-MCP routes.
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

      // New session — create a fresh server + transport pair
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => `session-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      });

      transport.onclose = () => {
        const sid = (transport as unknown as { sessionId?: string }).sessionId;
        if (sid) transports.delete(sid);
      };

      const server = serverFactory();
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
    res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    if (_req.method === "OPTIONS") { res.sendStatus(204); return; }
    next();
  });

  app.get("/api/status", (_req, res) => {
    const project = projectStore.getProject();
    const tasks = taskBoard.getAllTasks();
    const agents = taskBoard.getAllAgents();

    const progress = {
      total: tasks.length,
      accepted: tasks.filter((t) => t.status === "accepted").length,
      inProgress: tasks.filter((t) => ["assigned", "in_progress", "submitted"].includes(t.status)).length,
      pending: tasks.filter((t) => t.status === "pending").length,
      failed: tasks.filter((t) => ["rejected", "failed"].includes(t.status)).length,
    };

    res.json({
      project: project
        ? { id: project.id, name: project.name, description: project.description, status: project.status }
        : null,
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
      timestamp: new Date().toISOString(),
    });
  });

  app.get("/health", jsonParser, (_req, res) => {
    res.json({ status: "ok" });
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
