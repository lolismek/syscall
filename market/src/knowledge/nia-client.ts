import { createLogger } from "../utils/logger.js";

const log = createLogger("Nia");

const NIA_BASE = "https://apigcp.trynia.ai/v2";

export interface NiaEvent {
  timestamp: string;
  type: "index_repo" | "index_docs" | "search_project" | "search_general" | "search_web" | "web_research" | "lookup";
  source: "orchestrator" | "agent";
  detail: string;
  agentId?: string;
  status: "started" | "success" | "error";
  durationMs?: number;
}

const MAX_EVENTS = 100;

/** Global event log visible to all NiaClient instances */
const eventLog: NiaEvent[] = [];

function pushEvent(event: NiaEvent): void {
  eventLog.push(event);
  if (eventLog.length > MAX_EVENTS) {
    eventLog.splice(0, eventLog.length - MAX_EVENTS);
  }
}

export function getNiaEvents(): NiaEvent[] {
  return eventLog;
}

export class NiaClient {
  private apiKey: string;
  private agentId?: string;

  constructor(apiKey: string, agentId?: string) {
    this.apiKey = apiKey;
    this.agentId = agentId;
  }

  private async request(path: string, body: Record<string, unknown>): Promise<unknown> {
    const res = await fetch(`${NIA_BASE}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Nia API ${path} failed (${res.status}): ${text}`);
    }
    return res.json();
  }

  /**
   * Fire-and-forget: index a GitHub repo.
   * Does NOT block — logs errors but never throws.
   */
  indexRepoAsync(repository: string): void {
    log.info(`Indexing repo: ${repository}`);
    const event: NiaEvent = {
      timestamp: new Date().toISOString(),
      type: "index_repo",
      source: "orchestrator",
      detail: repository,
      status: "started",
    };
    pushEvent(event);

    const start = Date.now();
    this.request("/repositories", { repository })
      .then(() => {
        pushEvent({ ...event, timestamp: new Date().toISOString(), status: "success", durationMs: Date.now() - start });
      })
      .catch((err) => {
        log.warn(`Failed to index repo ${repository}: ${err}`);
        pushEvent({ ...event, timestamp: new Date().toISOString(), status: "error", detail: `${repository}: ${err}`, durationMs: Date.now() - start });
      });
  }

  /**
   * Fire-and-forget: index a documentation site.
   * Does NOT block — logs errors but never throws.
   */
  indexDocsAsync(url: string): void {
    log.info(`Indexing docs: ${url}`);
    const event: NiaEvent = {
      timestamp: new Date().toISOString(),
      type: "index_docs",
      source: "orchestrator",
      detail: url,
      status: "started",
    };
    pushEvent(event);

    const start = Date.now();
    this.request("/data-sources", { url })
      .then(() => {
        pushEvent({ ...event, timestamp: new Date().toISOString(), status: "success", durationMs: Date.now() - start });
      })
      .catch((err) => {
        log.warn(`Failed to index docs ${url}: ${err}`);
        pushEvent({ ...event, timestamp: new Date().toISOString(), status: "error", detail: `${url}: ${err}`, durationMs: Date.now() - start });
      });
  }

  /**
   * Semantic search across indexed sources.
   * Scoped to specific repos/data sources to prevent cross-project contamination.
   */
  async search(
    query: string,
    opts?: { repositories?: string[]; data_sources?: string[] },
  ): Promise<string> {
    const isScoped = !!(opts?.repositories || opts?.data_sources);
    const eventType = isScoped ? "search_project" as const : "search_general" as const;
    const event: NiaEvent = {
      timestamp: new Date().toISOString(),
      type: eventType,
      source: this.agentId ? "agent" : "orchestrator",
      detail: query,
      agentId: this.agentId,
      status: "started",
    };
    pushEvent(event);

    const start = Date.now();
    const body: Record<string, unknown> = { query };
    if (opts?.repositories) body.repositories = opts.repositories;
    if (opts?.data_sources) body.data_sources = opts.data_sources;

    try {
      const result = (await this.request("/universal-search", body)) as Record<string, unknown>;
      const text = typeof result === "object" && result !== null
        ? JSON.stringify(result, null, 2)
        : String(result);
      pushEvent({ ...event, timestamp: new Date().toISOString(), status: "success", durationMs: Date.now() - start });
      return text;
    } catch (err) {
      pushEvent({ ...event, timestamp: new Date().toISOString(), status: "error", detail: `${query}: ${err}`, durationMs: Date.now() - start });
      throw err;
    }
  }

  /**
   * Web search — general coding knowledge.
   * Does NOT touch any indexed repos, so no cross-project contamination.
   */
  async webSearch(query: string): Promise<string> {
    const event: NiaEvent = {
      timestamp: new Date().toISOString(),
      type: "search_web",
      source: this.agentId ? "agent" : "orchestrator",
      detail: query,
      agentId: this.agentId,
      status: "started",
    };
    pushEvent(event);

    const start = Date.now();
    try {
      const result = (await this.request("/web-search", { query })) as Record<string, unknown>;
      const text = typeof result === "object" && result !== null
        ? JSON.stringify(result, null, 2)
        : String(result);
      pushEvent({ ...event, timestamp: new Date().toISOString(), status: "success", durationMs: Date.now() - start });
      return text;
    } catch (err) {
      pushEvent({ ...event, timestamp: new Date().toISOString(), status: "error", detail: `${query}: ${err}`, durationMs: Date.now() - start });
      throw err;
    }
  }
}
