import type { Database } from "bun:sqlite";
import fs from "fs/promises";
import { Project } from "../types/project.js";
import { TaskBoard } from "./task-board.js";
import { GitRepo } from "../git/repo.js";
import { createLogger } from "../utils/logger.js";
import path from "path";

const log = createLogger("Registry");

export interface ProjectContext {
  project: Project;
  taskBoard: TaskBoard;
  gitRepo: GitRepo;
}

export class ProjectRegistry {
  private projects = new Map<string, ProjectContext>();
  private db: Database;

  constructor(db: Database) {
    this.db = db;
  }

  register(project: Project, taskBoard: TaskBoard, gitRepo: GitRepo): ProjectContext {
    const ctx: ProjectContext = { project, taskBoard, gitRepo };
    this.projects.set(project.id, ctx);
    log.info(`Registered project: ${project.id} — ${project.name}`);
    return ctx;
  }

  get(projectId: string): ProjectContext | undefined {
    return this.projects.get(projectId);
  }

  list(): ProjectContext[] {
    return Array.from(this.projects.values());
  }

  /** Delete a project: remove from DB, in-memory map, and workspace directory */
  async delete(projectId: string, workspacePath: string): Promise<void> {
    // Delete from DB (order matters for foreign keys)
    this.db.run("DELETE FROM nia_events WHERE project_id = ?", [projectId]);
    this.db.run("DELETE FROM agents WHERE project_id = ?", [projectId]);
    this.db.run("DELETE FROM tasks WHERE project_id = ?", [projectId]);
    this.db.run("DELETE FROM projects WHERE id = ?", [projectId]);

    // Remove from in-memory map
    this.projects.delete(projectId);

    // Remove workspace directory
    const projectDir = path.join(workspacePath, projectId);
    try {
      await fs.rm(projectDir, { recursive: true, force: true });
    } catch (err) {
      log.warn(`Failed to remove workspace dir ${projectDir}: ${err}`);
    }

    log.info(`Deleted project: ${projectId}`);
  }

  /** Hydrate all projects from the database and reconstruct runtime objects */
  async hydrateAll(workspacePath: string): Promise<void> {
    const rows = this.db.query("SELECT * FROM projects").all() as any[];

    for (const row of rows) {
      try {
        const project: Project = {
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

        // Reconstruct runtime objects
        const taskBoard = new TaskBoard(this.db, project.id);
        if (row.short_id) {
          taskBoard.setProjectShortId(row.short_id);
        }

        const projectDir = path.join(workspacePath, project.id);
        const gitRepo = new GitRepo(projectDir);
        await gitRepo.initRepo({ fresh: false });

        this.register(project, taskBoard, gitRepo);
        log.info(`Hydrated project: ${project.id} — ${project.name}`);
      } catch (err) {
        log.warn(`Failed to hydrate project ${row.id}: ${err}`);
      }
    }

    log.info(`Hydrated ${this.projects.size} project(s) from database`);
  }
}
