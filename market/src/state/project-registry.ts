import fs from "fs/promises";
import path from "path";
import { Project } from "../types/project.js";
import { TaskBoard } from "./task-board.js";
import { GitRepo } from "../git/repo.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("Registry");

export interface ProjectContext {
  project: Project;
  taskBoard: TaskBoard;
  gitRepo: GitRepo;
}

export class ProjectRegistry {
  private projects = new Map<string, ProjectContext>();

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

  /** Scan workspace for existing project state files and hydrate them */
  async hydrateAll(workspacePath: string): Promise<void> {
    let entries: string[];
    try {
      entries = await fs.readdir(workspacePath);
    } catch {
      log.info("No workspace directory found — nothing to hydrate");
      return;
    }

    for (const entry of entries) {
      const projectDir = path.join(workspacePath, entry);
      const statePath = path.join(projectDir, "state.json");

      try {
        const stat = await fs.stat(statePath);
        if (!stat.isFile()) continue;
      } catch {
        continue;
      }

      try {
        const raw = await fs.readFile(statePath, "utf-8");
        const savedState = JSON.parse(raw);

        if (!savedState.project) {
          log.warn(`State file ${statePath} has no project — skipping`);
          continue;
        }

        // Rehydrate project
        const project: Project = savedState.project;
        project.createdAt = new Date(project.createdAt);

        // Rehydrate task board
        const taskBoard = new TaskBoard();
        taskBoard.setSavePath(statePath);

        if (savedState.tasks && savedState.agents) {
          for (const [, task] of savedState.tasks) {
            task.createdAt = new Date(task.createdAt);
            task.updatedAt = new Date(task.updatedAt);
            task.lastActivityAt = task.lastActivityAt ? new Date(task.lastActivityAt) : task.updatedAt;
          }
          for (const [, agent] of savedState.agents) {
            agent.joinedAt = new Date(agent.joinedAt);
          }
          taskBoard.hydrate(savedState);
        }

        if (savedState.projectShortId) {
          taskBoard.setProjectShortId(savedState.projectShortId);
        }

        // Create git repo (non-destructive — repo already exists)
        const gitRepo = new GitRepo(projectDir);
        await gitRepo.initRepo({ fresh: false });

        this.register(project, taskBoard, gitRepo);
        log.info(`Hydrated project: ${project.id} — ${project.name}`);
      } catch (err) {
        log.warn(`Failed to hydrate project from ${statePath}: ${err}`);
      }
    }

    log.info(`Hydrated ${this.projects.size} project(s) from workspace`);
  }
}
