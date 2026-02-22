import type { Database } from "bun:sqlite";
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
  projectId?: string;
}

let _db: Database | null = null;

export function setNiaDb(db: Database): void {
  _db = db;
}

function pushEvent(event: NiaEvent): void {
  if (_db) {
    _db.run(
      `INSERT INTO nia_events (project_id, timestamp, type, source, detail, agent_id, status, duration_ms)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        event.projectId ?? null,
        event.timestamp,
        event.type,
        event.source,
        event.detail,
        event.agentId ?? null,
        event.status,
        event.durationMs ?? null,
      ],
    );
  }
}

export function getNiaEvents(projectId?: string): NiaEvent[] {
  if (!_db) return [];

  let rows: any[];
  if (projectId) {
    rows = _db.query(
      "SELECT * FROM nia_events WHERE project_id = ? AND status != 'started' ORDER BY id DESC LIMIT 200",
    ).all(projectId) as any[];
  } else {
    rows = _db.query("SELECT * FROM nia_events WHERE status != 'started' ORDER BY id DESC LIMIT 200").all() as any[];
  }

  return rows.map((r) => ({
    timestamp: r.timestamp,
    type: r.type,
    source: r.source,
    detail: r.detail,
    agentId: r.agent_id ?? undefined,
    status: r.status,
    durationMs: r.duration_ms ?? undefined,
    projectId: r.project_id ?? undefined,
  }));
}

// Timeouts tuned to actual Nia response times:
// - Indexing: server-side clone/process, can take minutes
// - Web/universal search: ~15-25s typical
// - Scoped query search with skip_llm: ~2-5s
const TIMEOUT_INDEX = 300_000;   // 5 min
const TIMEOUT_SEARCH = 45_000;   // 45s for web/universal
const TIMEOUT_QUERY = 15_000;    // 15s for scoped skip_llm queries

export class NiaClient {
  private apiKey: string;
  private agentId?: string;
  private projectId?: string;

  constructor(apiKey: string, agentId?: string, projectId?: string) {
    this.apiKey = apiKey;
    this.agentId = agentId;
    this.projectId = projectId;
  }

  private async request(path: string, body: Record<string, unknown>, timeoutMs: number, retries = 2): Promise<unknown> {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await fetch(`${NIA_BASE}${path}`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${this.apiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (!res.ok) {
          const text = await res.text().catch(() => "");
          throw new Error(`Nia API ${path} failed (${res.status}): ${text}`);
        }
        return res.json();
      } catch (err) {
        const isNetworkError = err instanceof TypeError;
        if (isNetworkError && attempt < retries) {
          const delay = (attempt + 1) * 2_000; // 2s, 4s
          log.debug(`Nia request ${path} failed (attempt ${attempt + 1}/${retries + 1}), retrying in ${delay}ms...`);
          await new Promise((r) => setTimeout(r, delay));
          continue;
        }
        throw err;
      }
    }
    throw new Error("unreachable");
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
      projectId: this.projectId,
    };
    pushEvent(event);

    const start = Date.now();
    this.request("/repositories", { repository }, TIMEOUT_INDEX)
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
      projectId: this.projectId,
    };
    pushEvent(event);

    const start = Date.now();
    this.request("/data-sources", { url }, TIMEOUT_INDEX)
      .then(() => {
        pushEvent({ ...event, timestamp: new Date().toISOString(), status: "success", durationMs: Date.now() - start });
      })
      .catch((err) => {
        log.warn(`Failed to index docs ${url}: ${err}`);
        pushEvent({ ...event, timestamp: new Date().toISOString(), status: "error", detail: `${url}: ${err}`, durationMs: Date.now() - start });
      });
  }

  /**
   * Scoped search — uses mode: "query" with messages format.
   * Scoped to specific repos/data sources to prevent cross-project contamination.
   * Uses skip_llm for fast raw results.
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
      projectId: this.projectId,
    };
    pushEvent(event);

    const start = Date.now();

    try {
      let result: Record<string, unknown>;

      if (isScoped) {
        // Use mode: "query" with messages — supports repositories/data_sources scoping
        const body: Record<string, unknown> = {
          mode: "query",
          messages: [{ role: "user", content: query }],
          skip_llm: true,
          fast_mode: true,
        };
        if (opts?.repositories) body.repositories = opts.repositories;
        if (opts?.data_sources) body.data_sources = opts.data_sources;
        result = (await this.request("/search", body, TIMEOUT_QUERY)) as Record<string, unknown>;
      } else {
        // Unscoped: use mode: "universal" for broad indexed knowledge search
        result = (await this.request("/search", { query, mode: "universal" }, TIMEOUT_SEARCH)) as Record<string, unknown>;
      }

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
   * Web search — general coding knowledge via mode: "web".
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
      projectId: this.projectId,
    };
    pushEvent(event);

    const start = Date.now();
    try {
      const result = (await this.request("/search", { query, mode: "web" }, TIMEOUT_SEARCH)) as Record<string, unknown>;
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
