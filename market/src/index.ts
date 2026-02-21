import "dotenv/config";
import fs from "fs/promises";
import path from "path";
import { TaskBoard } from "./state/task-board.js";
import { ProjectStore } from "./state/project-store.js";
import { GitRepo } from "./git/repo.js";
import { createMcpServerFactory } from "./mcp/server.js";
import { createTransport } from "./mcp/transport.js";
import { planProject, validateSubmission } from "./orchestrator/actions.js";
import { config } from "./utils/config.js";
import { createLogger } from "./utils/logger.js";

const log = createLogger("Main");

function parseArgs(): { projectIdea: string; fresh: boolean } {
  const args = process.argv.slice(2);
  let model: string | undefined;
  let projectIdea: string | undefined;
  let fresh = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--model" || args[i] === "-m") {
      model = args[++i];
    } else if (args[i] === "--fresh") {
      fresh = true;
    } else if (!projectIdea) {
      projectIdea = args[i];
    }
  }

  if (model) {
    config.model = model;
  }

  if (!projectIdea) {
    console.error(`Usage: npx tsx src/index.ts [--model <model>] [--fresh] "<project idea>"

Options:
  --model, -m    Anthropic model to use (default: ${config.model})
  --fresh        Force re-plan (deletes saved state)

Examples:
  npx tsx src/index.ts "Build a todo REST API"
  npx tsx src/index.ts --model claude-opus-4-5-20250514 "Build a chat app"
  npx tsx src/index.ts --fresh "Build a todo REST API"

Environment variables (set in .env or shell):
  ANTHROPIC_API_KEY   Required — your Anthropic API key
  MODEL               Default model (overridden by --model flag)
  PORT                MCP server port (default: 3100)
  WORKSPACE_PATH      Git workspace path (default: ./workspace)
  LOG_LEVEL           debug | info | warn | error (default: info)
  TASK_TIMEOUT_MS     Agent inactivity timeout in ms (default: 900000 = 15min)
  AGENT_API_KEY       Shared API key for /mcp auth (unset = no auth)
`);
    process.exit(1);
  }

  return { projectIdea, fresh };
}

async function main() {
  const { projectIdea, fresh } = parseArgs();

  // Validate API key is set
  if (!process.env.ANTHROPIC_API_KEY) {
    console.error("Error: ANTHROPIC_API_KEY is not set.");
    console.error("Set it in .env or export it in your shell:");
    console.error("  export ANTHROPIC_API_KEY=sk-ant-...");
    process.exit(1);
  }

  log.info("Starting Syscall Orchestrator...");
  log.info(`Model: ${config.model}`);
  log.info(`Workspace: ${config.workspacePath}`);
  log.info(`Port: ${config.port}`);
  log.info(`Auth: ${config.agentApiKey ? "enabled" : "disabled (no AGENT_API_KEY set)"}`);
  log.info(`Task timeout: ${config.taskTimeoutMs}ms`);

  // State file path — outside workspace/ since initRepo() wipes it
  const statePath = path.join(path.dirname(config.workspacePath), ".orchestrator-state.json");

  // Initialize shared state
  const taskBoard = new TaskBoard();
  const projectStore = new ProjectStore();
  const gitRepo = new GitRepo(config.workspacePath);

  // Set save paths for persistence
  taskBoard.setSavePath(statePath);
  projectStore.setSavePath(statePath);

  // Check for --fresh flag
  if (fresh) {
    try {
      await fs.unlink(statePath);
      log.info("Deleted state file (--fresh mode)");
    } catch {
      // File didn't exist, that's fine
    }
  }

  // Try to hydrate from saved state
  let hydrated = false;
  if (!fresh) {
    try {
      const raw = await fs.readFile(statePath, "utf-8");
      const savedState = JSON.parse(raw);

      if (savedState.tasks && savedState.agents) {
        // Rehydrate Date fields on tasks
        for (const [, task] of savedState.tasks) {
          task.createdAt = new Date(task.createdAt);
          task.updatedAt = new Date(task.updatedAt);
          task.lastActivityAt = task.lastActivityAt ? new Date(task.lastActivityAt) : task.updatedAt;
        }
        // Rehydrate Date fields on agents
        for (const [, agent] of savedState.agents) {
          agent.joinedAt = new Date(agent.joinedAt);
        }
        taskBoard.hydrate(savedState);
        hydrated = true;
        log.info("Hydrated task board from state file");
      }

      if (savedState.project) {
        projectStore.hydrateProject(savedState.project);
        hydrated = true;
        log.info("Hydrated project from state file");
      }
    } catch {
      // No state file or invalid — will plan from scratch
    }
  }

  if (!hydrated) {
    // Initialize git repo (wipes workspace)
    await gitRepo.initRepo();
    log.info("Git repo initialized");

    // Plan the project using the orchestrator
    log.info("Planning project...");
    const plan = await planProject(projectIdea, taskBoard, projectStore, gitRepo);
    log.info(`Project planned: ${plan.projectId}`);
    log.info(`Scaffold files: ${plan.scaffold.length}`);
    log.info(`Tasks created: ${plan.tasks.length}`);
  } else {
    log.info("Resuming from saved state — skipping planning");
  }

  // Print task summary
  const tasks = taskBoard.getAllTasks();
  console.log("\n=== Tasks ===");
  for (const task of tasks) {
    const deps = task.spec.dependencies.length > 0 ? ` (depends on: ${task.spec.dependencies.join(", ")})` : "";
    console.log(`  ${task.id}: ${task.spec.title} [${task.status}]${deps}`);
  }
  console.log("");

  // Wire up validation on task submission
  taskBoard.on("task_submitted", async (task) => {
    log.info(`Task submitted: ${task.id} — starting validation...`);
    try {
      const result = await validateSubmission(task.id, taskBoard, gitRepo);
      log.info(`Validation result for ${task.id}: ${result.accepted ? "ACCEPTED" : "REJECTED"}`);
      if (!result.accepted) {
        log.info(`Feedback: ${result.feedback}`);
      }
    } catch (err) {
      log.error(`Validation failed for ${task.id}: ${err}`);
      taskBoard.updateTaskStatus(task.id, "rejected", `Validation error: ${err}`);
    }
  });

  // Task timeout sweep — check every 60s for timed-out tasks
  setInterval(() => {
    const timedOut = taskBoard.getTimedOutTasks(config.taskTimeoutMs);
    for (const task of timedOut) {
      log.warn(`Task ${task.id} timed out (agent ${task.assignedTo} inactive for >${config.taskTimeoutMs}ms). Reassigning.`);
      taskBoard.reassignTask(task.id);
    }
  }, 60_000);

  // Start MCP server
  const mcpServerFactory = createMcpServerFactory(taskBoard, projectStore, gitRepo);
  const app = createTransport(mcpServerFactory, taskBoard, projectStore);

  app.listen(config.port, () => {
    log.info(`MCP server listening on http://localhost:${config.port}/mcp`);
    log.info("Waiting for agents to connect...");
  });
}

main().catch((err) => {
  log.error(`Fatal error: ${err}`);
  process.exit(1);
});
