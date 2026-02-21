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

const log = createLogger("CreateProject");

export async function createProject(
  projectIdea: string,
  registry: ProjectRegistry,
  githubClient: GitHubClient | null,
  workspacePath: string,
): Promise<ProjectContext> {
  const shortId = crypto.randomBytes(3).toString("hex");
  const projectId = `proj-${shortId}`;
  const projectDir = path.join(workspacePath, projectId);

  log.info(`Creating project ${projectId}...`);

  // Init git repo
  const gitRepo = new GitRepo(projectDir);
  await gitRepo.initRepo({ fresh: true });

  // Create task board with per-project state
  const taskBoard = new TaskBoard();
  const statePath = path.join(projectDir, "state.json");
  taskBoard.setSavePath(statePath);
  taskBoard.setProjectShortId(shortId);

  // Plan the project (LLM plans, writes scaffold, creates tasks)
  const plan = await planProject(projectIdea, projectId, taskBoard, gitRepo);
  log.info(`Project planned: ${plan.projectId}, ${plan.tasks.length} tasks`);

  // Create GitHub repo if configured
  let githubRepoUrl: string | null = null;
  let githubRepoName: string | null = null;
  if (githubClient) {
    const repoName = `${projectId}-${plan.projectId}`;
    try {
      const { cloneUrl, htmlUrl } = await githubClient.createRepo(
        repoName,
        projectIdea.slice(0, 200),
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

  // Create project object
  const now = new Date();
  const project: Project = {
    id: projectId,
    name: plan.projectId,
    description: projectIdea,
    createdAt: now,
    readyAt: new Date(now.getTime() + config.agentWaitMs),
    status: "active",
    githubRepoUrl,
    githubRepoName,
  };

  // Store project in task board (for persistence)
  taskBoard.setProject(project);

  // Register in registry
  const ctx = registry.register(project, taskBoard, gitRepo);

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

  return ctx;
}
