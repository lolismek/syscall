import { createLogger } from "../utils/logger.js";

const log = createLogger("Nia");

const NIA_BASE = "https://apigcp.trynia.ai/v2";

export class NiaClient {
  private apiKey: string;

  constructor(apiKey: string) {
    this.apiKey = apiKey;
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
    this.request("/repositories", { repository }).catch((err) => {
      log.warn(`Failed to index repo ${repository}: ${err}`);
    });
  }

  /**
   * Fire-and-forget: index a documentation site.
   * Does NOT block — logs errors but never throws.
   */
  indexDocsAsync(url: string): void {
    log.info(`Indexing docs: ${url}`);
    this.request("/data-sources", { url }).catch((err) => {
      log.warn(`Failed to index docs ${url}: ${err}`);
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
    const body: Record<string, unknown> = { query };
    if (opts?.repositories) body.repositories = opts.repositories;
    if (opts?.data_sources) body.data_sources = opts.data_sources;

    const result = (await this.request("/universal-search", body)) as Record<string, unknown>;
    // Return the result as a readable string for the agent
    if (typeof result === "object" && result !== null) {
      return JSON.stringify(result, null, 2);
    }
    return String(result);
  }

  /**
   * Web search — general coding knowledge.
   * Does NOT touch any indexed repos, so no cross-project contamination.
   */
  async webSearch(query: string): Promise<string> {
    const result = (await this.request("/web-search", { query })) as Record<string, unknown>;
    if (typeof result === "object" && result !== null) {
      return JSON.stringify(result, null, 2);
    }
    return String(result);
  }
}
