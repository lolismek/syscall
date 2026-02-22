import "dotenv/config";
import { ProjectRegistry } from "./state/project-registry.js";
import { GitHubClient } from "./git/github.js";
import { createMcpServerFactory } from "./mcp/server.js";
import { createTransport } from "./mcp/transport.js";
import { createProject } from "./orchestrator/create-project.js";
import { validateSubmission } from "./orchestrator/actions.js";
import { initDatabase, resetDatabase } from "./state/database.js";
import { setNiaDb } from "./knowledge/nia-client.js";
import { config } from "./utils/config.js";
import { createLogger } from "./utils/logger.js";
import { cleanupAll as cleanupEvolution } from "./state/evolution-manager.js";

const log = createLogger("Main");

function parseArgs(): { projectIdea: string | null; fresh: boolean } {
  const args = process.argv.slice(2);
  let model: string | undefined;
  let projectIdea: string | null = null;
  let fresh = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--model" || args[i] === "-m") {
      model = args[++i];
    } else if (args[i] === "--fresh") {
      fresh = true;
    } else if (args[i] === "--wait" || args[i] === "-w") {
      config.agentWaitMs = parseInt(args[++i], 10) * 1000;
    } else if (args[i] === "--recruiting" || args[i] === "-r") {
      config.recruitingDurationMs = parseInt(args[++i], 10) * 1000;
    } else if (args[i] === "--min-agents") {
      config.minAgents = parseInt(args[++i], 10);
    } else if (args[i] === "--help" || args[i] === "-h") {
      printUsage();
      process.exit(0);
    } else if (!projectIdea) {
      projectIdea = args[i];
    }
  }

  if (model) {
    config.model = model;
  }

  return { projectIdea, fresh };
}

function printUsage(): void {
  console.log(`Usage: bun src/index.ts [--model <model>] [--fresh] ["<project idea>"]

Modes:
  With idea:    Creates a project and starts the server
  Without idea: Starts the server only (create projects via dashboard or API)

Options:
  --model, -m    Anthropic model to use (default: ${config.model})
  --fresh        Force re-plan (wipes existing project state)
  --wait, -w     Seconds to wait for agents before assigning tasks (default: ${config.agentWaitMs / 1000}s)
  --recruiting, -r  Recruiting phase duration in seconds (default: ${config.recruitingDurationMs / 1000}s)
  --min-agents   Minimum agents to start early (default: ${config.minAgents})
  --help, -h     Show this help

Examples:
  bun start                                     # Server-only mode (dashboard at http://localhost:3100)
  bun start "Build a todo REST API"             # Create project + start server
  bun start -- --fresh "Build a todo API"
  curl -X POST localhost:3100/api/projects -H 'Content-Type: application/json' -d '{"idea":"Build a chat app"}'

Environment variables (set in .env or shell):
  ANTHROPIC_API_KEY   Required — your Anthropic API key
  MODEL               Default model (overridden by --model flag)
  PORT                MCP server port (default: 3100)
  WORKSPACE_PATH      Git workspace path (default: ./workspace)
  LOG_LEVEL           debug | info | warn | error (default: info)
  TASK_TIMEOUT_MS     Agent inactivity timeout in ms (default: 900000 = 15min)
  AGENT_WAIT_MS       Wait time in ms for agents to join before tasks start (default: 0)
  RECRUITING_DURATION_MS  Recruiting phase duration in ms (default: 120000 = 2min)
  MIN_AGENTS          Minimum agents to trigger early start (default: 1)
  AGENT_API_KEY       Shared API key for /mcp auth (unset = no auth)
  GITHUB_ORG          GitHub organization for auto-creating repos
  GITHUB_TOKEN        GitHub personal access token (required if GITHUB_ORG is set)
`);
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
  if (config.agentWaitMs > 0) {
    log.info(`Agent wait: ${config.agentWaitMs / 1000}s before task assignment`);
  }
  if (config.recruitingDurationMs > 0) {
    log.info(`Recruiting: ${config.recruitingDurationMs / 1000}s (min agents: ${config.minAgents})`);
  }

  // Initialize database
  const db = fresh
    ? resetDatabase(config.workspacePath)
    : initDatabase(config.workspacePath);

  // Wire Nia events to SQLite
  setNiaDb(db);

  // Initialize GitHub client if configured
  let githubClient: GitHubClient | null = null;
  if (config.githubOrg && config.githubToken) {
    githubClient = new GitHubClient(config.githubOrg, config.githubToken);
    log.info(`GitHub: ${config.githubOrg} (repos will be created automatically)`);
  } else {
    log.info("GitHub: disabled (set GITHUB_ORG and GITHUB_TOKEN to enable)");
  }

  // Create project registry backed by database
  const registry = new ProjectRegistry(db);

  // Hydrate existing projects from database (unless --fresh)
  if (!fresh) {
    await registry.hydrateAll(config.workspacePath);
    const existing = registry.list();
    if (existing.length > 0) {
      log.info(`Hydrated ${existing.length} existing project(s)`);
      for (const ctx of existing) {
        if (ctx.project.status === "stopped") {
          log.info(`Skipping stopped project: ${ctx.project.id}`);
          continue;
        }
        // Re-wire validation handlers for hydrated projects
        wireValidation(ctx);
      }
    }
  } else {
    log.info("Fresh mode — database reset");
  }

  // If CLI idea provided, create a project
  if (projectIdea) {
    log.info("Creating project from CLI...");
    const ctx = await createProject(projectIdea, registry, githubClient, config.workspacePath);
    log.info(`Project created: ${ctx.project.id} — ${ctx.project.name}`);

    // Print task summary
    const tasks = ctx.taskBoard.getAllTasks();
    console.log("\n=== Tasks ===");
    for (const task of tasks) {
      const deps = task.spec.dependencies.length > 0 ? ` (depends on: ${task.spec.dependencies.join(", ")})` : "";
      console.log(`  ${task.id}: ${task.spec.title} [${task.status}]${deps}`);
    }
    console.log("");
  }

  // Recruiting phase timer — check every 5s for projects ready to transition
  setInterval(() => {
    for (const ctx of registry.list()) {
      if (ctx.project.status === "stopped") continue;
      if (ctx.project.status !== "recruiting") continue;
      if (!ctx.project.recruitingUntil) continue;
      const until = new Date(ctx.project.recruitingUntil).getTime();
      if (Date.now() >= until) {
        ctx.project.status = "active";
        ctx.project.recruitingUntil = null;
        ctx.taskBoard.setProject(ctx.project);
        const agentCount = ctx.taskBoard.getAllAgents().length;
        log.info(`Recruiting ended for ${ctx.project.id} — ${agentCount} agent(s) joined. Project → active`);
      }
    }
  }, 5_000);

  // Task timeout sweep — check every 60s for timed-out tasks across all projects
  setInterval(() => {
    for (const ctx of registry.list()) {
      if (ctx.project.status === "stopped") continue;
      const timedOut = ctx.taskBoard.getTimedOutTasks(config.taskTimeoutMs);
      for (const task of timedOut) {
        log.warn(`Task ${task.id} timed out (agent ${task.assignedTo} inactive for >${config.taskTimeoutMs}ms). Reassigning.`);
        ctx.taskBoard.reassignTask(task.id);
      }
    }
  }, 60_000);

  // Start MCP server
  const mcpServerFactory = createMcpServerFactory(registry);
  const app = createTransport(mcpServerFactory, registry, githubClient);

  app.listen(config.port, () => {
    log.info(`Syscall Market running at http://localhost:${config.port}/`);
    log.info(`MCP endpoint: http://localhost:${config.port}/mcp`);
    if (!projectIdea) {
      log.info("Server-only mode — create projects via dashboard or POST /api/projects");
    }
    log.info("Waiting for agents to connect...");
  });

  // Cleanup evolution child processes on shutdown
  const shutdownHandler = () => {
    log.info("Shutting down — cleaning up evolution processes...");
    cleanupEvolution();
    process.exit(0);
  };
  process.on("SIGINT", shutdownHandler);
  process.on("SIGTERM", shutdownHandler);
}

/** Wire up task_submitted validation for a project context */
function wireValidation(ctx: import("./state/project-registry.js").ProjectContext): void {
  const { taskBoard, gitRepo } = ctx;

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
}

main().catch((err) => {
  log.error(`Fatal error: ${err}`);
  process.exit(1);
});
