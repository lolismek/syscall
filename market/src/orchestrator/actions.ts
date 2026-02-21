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

  // Add tasks to the board
  const tasks: TaskSpec[] = plan.tasks.map((t) => ({
    title: t.title,
    description: t.description,
    instructions: t.instructions,
    filePaths: t.filePaths,
    dependencies: t.dependencies,
    interfaceContract: t.interfaceContract,
  }));

  for (const taskSpec of tasks) {
    taskBoard.addTask(projectId, taskSpec);
  }

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
