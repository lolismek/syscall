import crypto from "crypto";
import path from "path";
import { ProjectRegistry, ProjectContext } from "../state/project-registry.js";
import { TaskBoard } from "../state/task-board.js";
import { GitRepo } from "../git/repo.js";
import { GitHubClient } from "../git/github.js";
import { Project } from "../types/project.js";
import { planProject, validateSubmission } from "./actions.js";
import { config } from "../utils/config.js";
import { createLogger } from "../utils/logger.js";
import { NiaClient } from "../knowledge/nia-client.js";
import { getDb } from "../state/database.js";

const log = createLogger("CreateProject");

export interface CreateProjectOptions {
  recruitingDurationMs?: number;
  minAgents?: number;
}

/**
 * Phase 1: Create project shell immediately (sync).
 * Registers in DB with status "planning", returns a ProjectContext
 * that the dashboard can display right away.
 */
export async function initProject(
  projectIdea: string,
  registry: ProjectRegistry,
  workspacePath: string,
): Promise<ProjectContext> {
  const shortId = crypto.randomBytes(3).toString("hex");
  const projectId = `proj-${shortId}`;
  const projectDir = path.join(workspacePath, projectId);

  log.info(`Creating project ${projectId}...`);

  // Init git repo
  const gitRepo = new GitRepo(projectDir);
  await gitRepo.initRepo({ fresh: true });

  // Insert placeholder project row
  const db = getDb();
  const now = new Date();
  db.run(
    `INSERT INTO projects (id, name, description, created_at, ready_at, status, min_agents, nia_source_ids, next_task_num, short_id)
     VALUES (?, ?, ?, ?, ?, 'planning', 1, '[]', 1, ?)`,
    [projectId, projectIdea.slice(0, 80), projectIdea, now.toISOString(), now.toISOString(), shortId],
  );

  const taskBoard = new TaskBoard(db, projectId);
  taskBoard.setProjectShortId(shortId);

  const project: Project = {
    id: projectId,
    name: projectIdea.slice(0, 80),
    description: projectIdea,
    createdAt: now,
    readyAt: now,
    recruitingUntil: null,
    minAgents: 1,
    status: "planning",
    githubRepoUrl: null,
    githubRepoName: null,
  };

  const ctx = registry.register(project, taskBoard, gitRepo);
  return ctx;
}

/**
 * Phase 2: Plan the project via LLM, set up GitHub, transition to recruiting/active.
 * Runs in background — the project is already visible on the dashboard.
 */
export async function planAndFinalize(
  ctx: ProjectContext,
  githubClient: GitHubClient | null,
  workspacePath: string,
  options?: CreateProjectOptions,
): Promise<void> {
  const { project, taskBoard, gitRepo } = ctx;
  const projectId = project.id;

  try {
    // Plan the project (LLM plans, writes scaffold, creates tasks)
    const plan = await planProject(project.description, projectId, taskBoard, gitRepo);
    log.info(`Project planned: ${plan.projectId}, ${plan.tasks.length} tasks`);

    // Create GitHub repo if configured
    let githubRepoUrl: string | null = null;
    let githubRepoName: string | null = null;
    if (githubClient) {
      const repoName = `${projectId}-${plan.projectId}`;
      try {
        const { cloneUrl, htmlUrl } = await githubClient.createRepo(
          repoName,
          project.description.slice(0, 200),
        );
        githubRepoUrl = htmlUrl;
        githubRepoName = repoName;
        await gitRepo.addRemote("origin", cloneUrl);
        await gitRepo.push("origin", "main");
        log.info(`GitHub repo created: ${htmlUrl}`);
      } catch (err) {
        log.warn(`Failed to create GitHub repo: ${err}`);
      }
    }

    // Fire-and-forget: index the project's GitHub repo via Nia.
    let niaRepoId: string | undefined;
    const niaSourceIds: string[] = [];
    if (config.niaApiKey && githubRepoName && config.githubOrg) {
      const nia = new NiaClient(config.niaApiKey, undefined, projectId);
      niaRepoId = `${config.githubOrg}/${githubRepoName}`;
      nia.indexRepoAsync(niaRepoId);
      log.info(`Nia: indexing project repo ${niaRepoId}`);
    }

    // Finalize project — transition from "planning" to recruiting/active
    const now = new Date();
    const recruitingDuration = options?.recruitingDurationMs ?? Math.max(config.recruitingDurationMs, config.agentWaitMs);
    const minAgents = options?.minAgents ?? config.minAgents;
    const recruitingUntil = recruitingDuration > 0 ? new Date(now.getTime() + recruitingDuration) : null;

    project.name = plan.projectId;
    project.readyAt = new Date(now.getTime() + recruitingDuration);
    project.recruitingUntil = recruitingUntil;
    project.minAgents = minAgents;
    project.status = recruitingDuration > 0 ? "recruiting" : "active";
    project.githubRepoUrl = githubRepoUrl;
    project.githubRepoName = githubRepoName;
    project.niaRepoId = niaRepoId;
    project.niaSourceIds = niaSourceIds;

    taskBoard.setProject(project);

    // Wire up validation on task submission
    taskBoard.on("task_submitted", async (task) => {
      log.info(`Task submitted: ${task.id} — starting validation...`);
      try {
        const result = await validateSubmission(task.id, taskBoard, gitRepo, githubClient);
        log.info(`Validation result for ${task.id}: ${result.accepted ? "ACCEPTED" : "REJECTED"}`);
        if (!result.accepted) {
          log.info(`Feedback: ${result.feedback}`);
        }
      } catch (err) {
        log.error(`Validation failed for ${task.id}: ${err}`);
        taskBoard.updateTaskStatus(task.id, "rejected", `Validation error: ${err}`);
      }
    });

    log.info(`Project ${projectId} ready — ${plan.tasks.length} tasks`);
  } catch (err) {
    log.error(`Planning failed for ${projectId}: ${err}`);
    // Mark project as failed so the dashboard shows the error
    project.status = "stopped";
    taskBoard.setProject(project);
  }
}

/**
 * Legacy helper: init + plan synchronously (used by CLI path).
 */
export async function createProject(
  projectIdea: string,
  registry: ProjectRegistry,
  githubClient: GitHubClient | null,
  workspacePath: string,
  options?: CreateProjectOptions,
): Promise<ProjectContext> {
  const ctx = await initProject(projectIdea, registry, workspacePath);
  await planAndFinalize(ctx, githubClient, workspacePath, options);
  return ctx;
}
