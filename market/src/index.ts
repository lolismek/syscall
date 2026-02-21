import "dotenv/config";
import { TaskBoard } from "./state/task-board.js";
import { ProjectStore } from "./state/project-store.js";
import { GitRepo } from "./git/repo.js";
import { createMcpServerFactory } from "./mcp/server.js";
import { createTransport } from "./mcp/transport.js";
import { planProject, validateSubmission } from "./orchestrator/actions.js";
import { config } from "./utils/config.js";
import { createLogger } from "./utils/logger.js";

const log = createLogger("Main");

function parseArgs(): { projectIdea: string } {
  const args = process.argv.slice(2);
  let model: string | undefined;
  let projectIdea: string | undefined;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--model" || args[i] === "-m") {
      model = args[++i];
    } else if (!projectIdea) {
      projectIdea = args[i];
    }
  }

  if (model) {
    config.model = model;
  }

  if (!projectIdea) {
    console.error(`Usage: npx tsx src/index.ts [--model <model>] "<project idea>"

Options:
  --model, -m    Anthropic model to use (default: ${config.model})

Examples:
  npx tsx src/index.ts "Build a todo REST API"
  npx tsx src/index.ts --model claude-opus-4-5-20250514 "Build a chat app"

Environment variables (set in .env or shell):
  ANTHROPIC_API_KEY   Required — your Anthropic API key
  MODEL               Default model (overridden by --model flag)
  PORT                MCP server port (default: 3100)
  WORKSPACE_PATH      Git workspace path (default: ./workspace)
  LOG_LEVEL           debug | info | warn | error (default: info)
`);
    process.exit(1);
  }

  return { projectIdea };
}

async function main() {
  const { projectIdea } = parseArgs();

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

  // Initialize shared state
  const taskBoard = new TaskBoard();
  const projectStore = new ProjectStore();
  const gitRepo = new GitRepo(config.workspacePath);

  // Initialize git repo
  await gitRepo.initRepo();
  log.info("Git repo initialized");

  // Plan the project using the orchestrator
  log.info("Planning project...");
  const plan = await planProject(projectIdea, taskBoard, projectStore, gitRepo);
  log.info(`Project planned: ${plan.projectId}`);
  log.info(`Scaffold files: ${plan.scaffold.length}`);
  log.info(`Tasks created: ${plan.tasks.length}`);

  // Print task summary
  const tasks = taskBoard.getAllTasks();
  console.log("\n=== Tasks ===");
  for (const task of tasks) {
    const deps = task.spec.dependencies.length > 0 ? ` (depends on: ${task.spec.dependencies.join(", ")})` : "";
    console.log(`  ${task.id}: ${task.spec.title}${deps}`);
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

  // Start MCP server
  const mcpServerFactory = createMcpServerFactory(taskBoard, projectStore, gitRepo);
  const app = createTransport(mcpServerFactory);

  app.listen(config.port, () => {
    log.info(`MCP server listening on http://localhost:${config.port}/mcp`);
    log.info("Waiting for agents to connect...");
  });
}

main().catch((err) => {
  log.error(`Fatal error: ${err}`);
  process.exit(1);
});
