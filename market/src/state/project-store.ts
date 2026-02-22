import type { Database } from "bun:sqlite";
import { Project } from "../types/project.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("ProjectStore");

export class ProjectStore {
  private db: Database;
  private projectId: string;

  constructor(db: Database, projectId: string) {
    this.db = db;
    this.projectId = projectId;
  }

  setProject(project: Project): void {
    this.db.run(
      `INSERT OR REPLACE INTO projects (id, name, description, created_at, ready_at, recruiting_until, min_agents, status, github_repo_url, github_repo_name, nia_repo_id, nia_source_ids, next_task_num, short_id)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT next_task_num FROM projects WHERE id = ?), 1), (SELECT short_id FROM projects WHERE id = ?))`,
      [
        project.id,
        project.name,
        project.description,
        project.createdAt.toISOString(),
        project.readyAt.toISOString(),
        project.recruitingUntil ? project.recruitingUntil.toISOString() : null,
        project.minAgents,
        project.status,
        project.githubRepoUrl,
        project.githubRepoName,
        project.niaRepoId ?? null,
        project.niaSourceIds ? JSON.stringify(project.niaSourceIds) : "[]",
        project.id,
        project.id,
      ],
    );
    log.info(`Project set: ${project.id} — ${project.name}`);
  }

  getProject(): Project | null {
    const row = this.db.query("SELECT * FROM projects WHERE id = ?").get(this.projectId) as any;
    if (!row) return null;
    return {
      id: row.id,
      name: row.name,
      description: row.description,
      createdAt: new Date(row.created_at),
      readyAt: new Date(row.ready_at),
      recruitingUntil: row.recruiting_until ? new Date(row.recruiting_until) : null,
      minAgents: row.min_agents,
      status: row.status,
      githubRepoUrl: row.github_repo_url,
      githubRepoName: row.github_repo_name,
      niaRepoId: row.nia_repo_id ?? undefined,
      niaSourceIds: row.nia_source_ids ? JSON.parse(row.nia_source_ids) : undefined,
    };
  }

  updateStatus(status: Project["status"]): void {
    this.db.run("UPDATE projects SET status = ? WHERE id = ?", [status, this.projectId]);
    log.info(`Project status → ${status}`);
  }
}
