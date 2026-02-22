import { invokeOrchestrator } from "./invoke.js";
import { buildPlanPrompt } from "./prompts/plan-project.js";
import { buildValidationPrompt } from "./prompts/validate-submission.js";
import { TaskBoard } from "../state/task-board.js";
import { GitRepo } from "../git/repo.js";
import { GitHubClient } from "../git/github.js";
import { ProjectPlan, ScaffoldFile, Project } from "../types/project.js";
import { TaskSpec } from "../types/task.js";
import { createLogger } from "../utils/logger.js";
import { config } from "../utils/config.js";
import { NiaClient } from "../knowledge/nia-client.js";

const log = createLogger("Actions");

/** Track merges per project so we only re-index every 5th merge */
const mergeCounters = new Map<string, number>();

interface PlanResponse {
  projectName: string;
  scaffold: ScaffoldFile[];
  tasks: Array<{
    title: string;
    description: string;
    instructions: string;
    filePaths: string[];
    dependencies: string[];
    interfaceContract: string;
  }>;
}

interface ValidationResponse {
  accepted: boolean;
  feedback: string;
  issues: string[];
}

function extractJson(text: string): string {
  // Find the first '{' and last '}' to extract the outermost JSON object.
  // This avoids issues with nested markdown fences (e.g. ```bash inside README content)
  // that break regex-based fence extraction.
  const firstBrace = text.indexOf("{");
  const lastBrace = text.lastIndexOf("}");
  if (firstBrace !== -1 && lastBrace > firstBrace) {
    return text.slice(firstBrace, lastBrace + 1);
  }
  return text;
}

export async function planProject(
  projectIdea: string,
  projectId: string,
  taskBoard: TaskBoard,
  gitRepo: GitRepo
): Promise<ProjectPlan> {
  log.info("Planning project...", { idea: projectIdea.slice(0, 100) });

  // Enrich planning with Nia web search for documentation context
  let documentationContext: string | undefined;
  if (config.niaApiKey) {
    try {
      const nia = new NiaClient(config.niaApiKey);
      log.info("Researching project technologies via Nia...");
      documentationContext = await nia.webSearch(projectIdea);
      log.info("Nia research complete, enriching planning prompt");
    } catch (err) {
      log.warn(`Nia web search failed (continuing without): ${err}`);
    }
  }

  const prompt = buildPlanPrompt(projectIdea, documentationContext);
  const rawResult = await invokeOrchestrator(prompt);

  let plan: PlanResponse;
  try {
    plan = JSON.parse(extractJson(rawResult));
  } catch (err) {
    log.error("Failed to parse plan JSON", { raw: rawResult.slice(0, 500) });
    throw new Error(`Orchestrator returned invalid JSON: ${err}`);
  }

  // Write scaffold files to main
  for (const file of plan.scaffold) {
    await gitRepo.writeFile(file.path, file.content);
  }
  try {
    await gitRepo.commitOnMain(`Scaffold: ${plan.projectName}`);
  } catch (err) {
    log.warn(`Scaffold commit failed (may be empty): ${err}`);
  }

  // Add tasks to the board, remapping LLM-generated dependency IDs to real IDs.
  const createdTasks: import("../types/task.js").Task[] = [];
  for (const t of plan.tasks) {
    const task = taskBoard.addTask(projectId, {
      title: t.title,
      description: t.description,
      instructions: t.instructions,
      filePaths: t.filePaths,
      dependencies: [],  // placeholder — remapped below
      interfaceContract: t.interfaceContract,
    });
    createdTasks.push(task);
  }

  // Build a mapping from any LLM-style reference for task N → real ID.
  const idMap = new Map<string, string>();
  for (let i = 0; i < createdTasks.length; i++) {
    const realId = createdTasks[i].id;
    const n = i + 1;
    const padded = String(n).padStart(3, "0");
    for (const prefix of ["task-", "task_", "task", ""]) {
      idMap.set(`${prefix}${n}`, realId);
      idMap.set(`${prefix}${padded}`, realId);
    }
  }

  function resolveDepId(raw: string): string | null {
    const key = raw.trim().toLowerCase();
    return idMap.get(key) ?? null;
  }

  // Now patch dependencies on each created task
  for (let i = 0; i < plan.tasks.length; i++) {
    const rawDeps = plan.tasks[i].dependencies;
    const resolved: string[] = [];
    for (const raw of rawDeps) {
      const realId = resolveDepId(raw);
      if (realId) {
        resolved.push(realId);
      } else {
        log.warn(`Task ${createdTasks[i].id}: unknown dependency "${raw}" — skipping`);
      }
    }
    createdTasks[i].spec.dependencies = resolved;
  }

  // Cycle detection — break any circular dependencies
  let cycleCheck = taskBoard.hasCyclicDependencies();
  while (cycleCheck.hasCycle && cycleCheck.cycle) {
    const cycle = cycleCheck.cycle;
    log.warn(`Circular dependency detected: ${cycle.join(" → ")}. Breaking cycle.`);
    const targetId = cycle[cycle.length - 2];
    const depId = cycle[cycle.length - 1];
    const target = taskBoard.getTask(targetId);
    if (target) {
      target.spec.dependencies = target.spec.dependencies.filter((d) => d !== depId);
      log.warn(`Removed dependency ${depId} from ${targetId}`);
    }
    cycleCheck = taskBoard.hasCyclicDependencies();
  }

  const tasks: TaskSpec[] = createdTasks.map((t) => t.spec);

  log.info(`Project planned: ${plan.projectName}, ${tasks.length} tasks created`);

  return {
    projectId: plan.projectName,
    scaffold: plan.scaffold,
    tasks,
    sharedTypes: plan.scaffold
      .filter((f) => f.path.includes("types") || f.path.includes("interfaces"))
      .map((f) => f.content)
      .join("\n"),
  };
}

export async function validateSubmission(
  taskId: string,
  taskBoard: TaskBoard,
  gitRepo: GitRepo,
  githubClient?: GitHubClient | null,
): Promise<{ accepted: boolean; feedback: string }> {
  const task = taskBoard.getTask(taskId);
  if (!task) throw new Error(`Task ${taskId} not found`);

  log.info(`Validating submission for task ${taskId}...`);

  // Fetch from origin if GitHub is configured
  if (githubClient) {
    try {
      await gitRepo.fetch("origin");
    } catch (err) {
      log.debug(`Fetch from origin failed (may not have remote): ${err}`);
    }
  }

  // Scope check: reject if the agent modified files outside its assigned filePaths
  if (task.branch) {
    const mergeRef = githubClient ? `origin/${task.branch}` : task.branch;
    const changedFiles = await gitRepo.getChangedFiles(mergeRef);
    const allowedSet = new Set(task.spec.filePaths);
    const outOfScope = changedFiles.filter((f) => !allowedSet.has(f));
    if (outOfScope.length > 0) {
      log.warn(`Task ${taskId}: agent modified out-of-scope files: ${outOfScope.join(", ")}`);
      const feedback = `Submission rejected: you modified files outside your assigned scope. ` +
        `Allowed files: [${task.spec.filePaths.join(", ")}]. ` +
        `Out-of-scope files: [${outOfScope.join(", ")}]. ` +
        `Please revert changes to out-of-scope files and resubmit.`;
      taskBoard.updateTaskStatus(taskId, "rejected", feedback);
      return { accepted: false, feedback };
    }
  }

  const diff = task.submissionDiff || "";
  if (!diff) {
    log.warn(`No diff found for task ${taskId}`);
    const feedback = "No code changes detected. Please make sure you committed your changes to the assigned branch.";
    taskBoard.updateTaskStatus(taskId, "rejected", feedback);
    return { accepted: false, feedback };
  }

  const prompt = buildValidationPrompt(
    task.spec.title,
    task.spec.instructions,
    task.spec.interfaceContract,
    diff
  );

  const rawResult = await invokeOrchestrator(prompt);

  let validation: ValidationResponse;
  try {
    validation = JSON.parse(extractJson(rawResult));
  } catch (err) {
    log.error("Failed to parse validation JSON", { raw: rawResult.slice(0, 500) });
    const feedback = "Validation failed due to an internal error. Please resubmit.";
    taskBoard.updateTaskStatus(taskId, "rejected", feedback);
    return { accepted: false, feedback };
  }

  if (validation.accepted) {
    if (task.branch) {
      // Use origin/ prefix when GitHub is configured because the branch
      // only exists on the remote, not locally
      const mergeRef = githubClient ? `origin/${task.branch}` : task.branch;
      try {
        log.info(`Merging branch ${mergeRef} into main for task ${taskId}...`);
        await gitRepo.mergeBranch(mergeRef);
        log.info(`MERGE OK — ${mergeRef} merged to main (task ${taskId}: "${task.spec.title}")`);

        // Push to GitHub if configured
        if (githubClient) {
          try {
            await gitRepo.push("origin", "main");
            log.info(`PUSH OK — main pushed to GitHub (task ${taskId})`);
          } catch (err) {
            log.warn(`PUSH FAILED for task ${taskId}: ${err}`);
          }
        }
      } catch (err) {
        log.error(`MERGE FAILED — ${task.branch} (task ${taskId}): ${err}`);
        const feedback = `Merge conflict. Please rebase your branch on main and resubmit. Error: ${err}`;
        taskBoard.updateTaskStatus(taskId, "rejected", feedback);
        return { accepted: false, feedback };
      }
    }
    taskBoard.updateTaskStatus(taskId, "accepted", validation.feedback);
    log.info(`Task ${taskId} ACCEPTED — "${task.spec.title}"`);

    // Re-index project repo every 5 merges so Nia stays reasonably fresh
    const project = taskBoard.getProject();
    if (project?.niaRepoId && config.niaApiKey) {
      const projectId = project.id;
      const count = (mergeCounters.get(projectId) ?? 0) + 1;
      mergeCounters.set(projectId, count);
      if (count % 5 === 0) {
        log.info(`Re-indexing project repo in Nia (merge #${count})`);
        const nia = new NiaClient(config.niaApiKey);
        nia.indexRepoAsync(project.niaRepoId);
      }
    }
  } else {
    taskBoard.updateTaskStatus(taskId, "rejected", validation.feedback);
    log.info(`Task ${taskId} REJECTED — "${task.spec.title}": ${validation.feedback}`);
  }

  return {
    accepted: validation.accepted,
    feedback: validation.feedback,
  };
}
