import express from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { McpServerFactory } from "./server.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("Transport");

export function createTransport(serverFactory: McpServerFactory): express.Express {
  const app = express();
  // IMPORTANT: Do NOT use express.json() globally — the MCP StreamableHTTP
  // transport needs the raw body to parse JSON-RPC itself. Only parse JSON
  // on non-MCP routes.
  const jsonParser = express.json();

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
