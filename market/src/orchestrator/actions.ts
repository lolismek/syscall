import { invokeOrchestrator } from "./invoke.js";
import { buildPlanPrompt } from "./prompts/plan-project.js";
import { buildValidationPrompt } from "./prompts/validate-submission.js";
import { TaskBoard } from "../state/task-board.js";
import { ProjectStore } from "../state/project-store.js";
import { GitRepo } from "../git/repo.js";
import { ProjectPlan, ScaffoldFile } from "../types/project.js";
import { TaskSpec } from "../types/task.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("Actions");

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
  // Try to extract JSON from the response — handle markdown fences
  const fenceMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
  if (fenceMatch) return fenceMatch[1].trim();
  // Try raw JSON
  const jsonMatch = text.match(/\{[\s\S]*\}/);
  if (jsonMatch) return jsonMatch[0];
  return text;
}

export async function planProject(
  projectIdea: string,
  taskBoard: TaskBoard,
  projectStore: ProjectStore,
  gitRepo: GitRepo
): Promise<ProjectPlan> {
  log.info("Planning project...", { idea: projectIdea.slice(0, 100) });

  const prompt = buildPlanPrompt(projectIdea);
  const rawResult = await invokeOrchestrator(prompt);

  let plan: PlanResponse;
  try {
    plan = JSON.parse(extractJson(rawResult));
  } catch (err) {
    log.error("Failed to parse plan JSON", { raw: rawResult.slice(0, 500) });
    throw new Error(`Orchestrator returned invalid JSON: ${err}`);
  }

  // Create the project
  const projectId = plan.projectName;
  projectStore.setProject({
    id: projectId,
    name: plan.projectName,
    description: projectIdea,
    createdAt: new Date(),
    status: "active",
  });

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
  // The LLM is told to use "task-001" style IDs but may produce variants like
  // "task-1", "task_001", "task_1", or even just "1".
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
  // Task at index 0 is task #1, index 1 is task #2, etc.
  const idMap = new Map<string, string>();
  for (let i = 0; i < createdTasks.length; i++) {
    const realId = createdTasks[i].id;
    const n = i + 1;
    // Register every plausible variant the LLM might use
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
    // Remove the back-edge: last dependency link in the cycle
    const targetId = cycle[cycle.length - 2]; // task that has the offending dep
    const depId = cycle[cycle.length - 1];     // the dep that creates the cycle
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
    projectId,
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
  gitRepo: GitRepo
): Promise<{ accepted: boolean; feedback: string }> {
  const task = taskBoard.getTask(taskId);
  if (!task) throw new Error(`Task ${taskId} not found`);

  log.info(`Validating submission for task ${taskId}...`);

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
    // Default to rejection on parse failure
    const feedback = "Validation failed due to an internal error. Please resubmit.";
    taskBoard.updateTaskStatus(taskId, "rejected", feedback);
    return { accepted: false, feedback };
  }

  if (validation.accepted) {
    // Merge the branch
    if (task.branch) {
      try {
        await gitRepo.mergeBranch(task.branch);
        log.info(`Merged branch ${task.branch} to main`);
      } catch (err) {
        log.error(`Failed to merge branch ${task.branch}: ${err}`);
        const feedback = `Merge conflict. Please rebase your branch on main and resubmit. Error: ${err}`;
        taskBoard.updateTaskStatus(taskId, "rejected", feedback);
        return { accepted: false, feedback };
      }
    }
    taskBoard.updateTaskStatus(taskId, "accepted", validation.feedback);
    log.info(`Task ${taskId} ACCEPTED`);
  } else {
    taskBoard.updateTaskStatus(taskId, "rejected", validation.feedback);
    log.info(`Task ${taskId} REJECTED: ${validation.feedback}`);
  }

  return {
    accepted: validation.accepted,
    feedback: validation.feedback,
  };
}
