import { createLogger } from "../utils/logger.js";

const log = createLogger("GitHub");

export class GitHubClient {
  constructor(
    private org: string,
    private token: string,
  ) {}

  private async api(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<{ status: number; data: unknown }> {
    const url = `https://api.github.com${path}`;
    log.debug(`${method} ${url}`);
    const res = await fetch(url, {
      method,
      headers: {
        Authorization: `token ${this.token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = res.status === 204 ? null : await res.json();
    if (!res.ok && res.status !== 404) {
      log.warn(`GitHub API ${method} ${path} → ${res.status}: ${JSON.stringify(data)}`);
    }
    return { status: res.status, data };
  }

  async createRepo(
    name: string,
    description: string,
  ): Promise<{ cloneUrl: string; htmlUrl: string }> {
    const { status, data } = await this.api("POST", `/orgs/${this.org}/repos`, {
      name,
      description,
      private: false,
      auto_init: false,
    });
    if (status !== 201) {
      throw new Error(`Failed to create repo ${this.org}/${name}: ${JSON.stringify(data)}`);
    }
    const repo = data as { clone_url: string; html_url: string };
    log.info(`Created repo: ${repo.html_url}`);
    return { cloneUrl: repo.clone_url, htmlUrl: repo.html_url };
  }

  async deleteRepo(name: string): Promise<void> {
    const { status } = await this.api("DELETE", `/repos/${this.org}/${name}`);
    if (status !== 204) {
      log.warn(`Failed to delete repo ${this.org}/${name} (status ${status})`);
    } else {
      log.info(`Deleted repo: ${this.org}/${name}`);
    }
  }

  async repoExists(name: string): Promise<boolean> {
    const { status } = await this.api("GET", `/repos/${this.org}/${name}`);
    return status === 200;
  }

  getOrg(): string {
    return this.org;
  }
}
