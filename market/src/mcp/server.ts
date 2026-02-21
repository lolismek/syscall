import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { ProjectRegistry } from "../state/project-registry.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("MCP");

export interface SessionContext {
  projectId: string | null;
  agentId: string | null;
}

export type McpServerFactory = (sessionCtx: SessionContext) => McpServer;

export function createMcpServerFactory(
  registry: ProjectRegistry,
): McpServerFactory {
  return (sessionCtx: SessionContext) => createMcpServer(registry, sessionCtx);
}

/** Per-agent submission rate limiter: max 3 submissions per 60s window */
const SUBMIT_RATE_LIMIT = 3;
const SUBMIT_RATE_WINDOW_MS = 60_000;
const submitTimestamps = new Map<string, number[]>();

function isSubmitRateLimited(agentId: string): boolean {
  const now = Date.now();
  const timestamps = submitTimestamps.get(agentId) ?? [];
  const recent = timestamps.filter((t) => now - t < SUBMIT_RATE_WINDOW_MS);
  if (recent.length >= SUBMIT_RATE_LIMIT) {
    submitTimestamps.set(agentId, recent);
    return true;
  }
  recent.push(now);
  submitTimestamps.set(agentId, recent);
  return false;
}

function createMcpServer(
  registry: ProjectRegistry,
  sessionCtx: SessionContext,
): McpServer {
  const server = new McpServer({
    name: "syscall-orchestrator",
    version: "0.1.0",
  });

  /** Helper: get the bound project context or return null */
  function getProjectCtx() {
    if (!sessionCtx.projectId) return null;
    return registry.get(sessionCtx.projectId) ?? null;
  }

  /** Try to recover session by looking up which project owns this agent */
  function recoverSession(agentId: string): ReturnType<typeof getProjectCtx> {
    if (getProjectCtx()) return getProjectCtx();
    for (const pctx of registry.list()) {
      if (pctx.taskBoard.getAgent(agentId)) {
        sessionCtx.projectId = pctx.project.id;
        sessionCtx.agentId = agentId;
        log.info(`Recovered session for agent ${agentId} → project ${pctx.project.id}`);
        return pctx;
      }
    }
    return null;
  }

  // --- Tool: list_projects ---
  server.tool(
    "list_projects",
    "List all active projects with summary info.",
    {},
    async () => {
      const projects = registry.list().map((ctx) => {
        const tasks = ctx.taskBoard.getAllTasks();
        const accepted = tasks.filter((t) => t.status === "accepted").length;
        return {
          id: ctx.project.id,
          name: ctx.project.name,
          description: ctx.project.description,
          status: ctx.project.status,
          githubUrl: ctx.project.githubRepoUrl,
          taskCount: tasks.length,
          acceptedCount: accepted,
        };
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(projects, null, 2) }] };
    }
  );

  // --- Tool: join_project ---
  server.tool(
    "join_project",
    "Register as a worker agent on a specific project. Clone the repoUrl yourself, then call get_my_task.",
    {
      project_id: z.string().describe("The project ID to join (from list_projects)"),
      agent_name: z.string().describe("Your name / identifier"),
      capabilities: z.array(z.string()).describe("What you can do, e.g. ['typescript', 'react']"),
    },
    async ({ project_id, agent_name, capabilities }) => {
      const ctx = registry.get(project_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Project not found: ${project_id}` }) }] };
      }

      const { project, taskBoard, gitRepo } = ctx;
      const agent = taskBoard.registerAgent(agent_name, capabilities, project_id);

      // Bind session to this project
      sessionCtx.projectId = project_id;
      sessionCtx.agentId = agent.id;

      const tasks = taskBoard.getAllTasks();
      const repoUrl = project.githubRepoUrl || gitRepo.getRepoPath();

      const summary = {
        agentId: agent.id,
        project: {
          id: project.id,
          name: project.name,
          description: project.description,
          status: project.status,
        },
        totalTasks: tasks.length,
        availableTasks: taskBoard.getAvailableTasks().length,
        repoUrl,
        rules: [
          "Clone repoUrl into your own working directory",
          "Call get_my_task to receive your assignment",
          "Call report_status when you start working",
          "Work on your assigned branch only",
          "Push your branch, then call submit_result — do NOT merge to main",
        ],
      };
      log.info(`Agent joined: ${agent.name} (${agent.id}) on project ${project_id}`);
      return { content: [{ type: "text" as const, text: JSON.stringify(summary, null, 2) }] };
    }
  );

  // --- Tool: get_my_task ---
  server.tool(
    "get_my_task",
    "Get the next available task assigned to you. Fetch origin and checkout the returned branch in your clone.",
    {
      agent_id: z.string().describe("Your agent ID from join_project"),
    },
    async ({ agent_id }) => {
      const resolvedCtx = recoverSession(agent_id);
      if (!resolvedCtx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { taskBoard, gitRepo, project } = resolvedCtx;

      const agent = taskBoard.getAgent(agent_id);
      if (!agent) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Unknown agent" }) }] };
      }

      // Touch current task as proof of life
      if (agent.currentTaskId) {
        taskBoard.touchTask(agent.currentTaskId);
      }

      // Check if project is still in the agent-wait period
      if (project.readyAt && Date.now() < new Date(project.readyAt).getTime()) {
        const remainingMs = new Date(project.readyAt).getTime() - Date.now();
        const remainingSec = Math.ceil(remainingMs / 1000);
        return {
          content: [{ type: "text" as const, text: JSON.stringify({
            message: `Project is waiting for agents to join. Task assignment begins in ${remainingSec}s. Call get_my_task again after that.`,
            readyAt: project.readyAt,
            remainingSeconds: remainingSec,
          }, null, 2) }],
        };
      }

      // If agent already has a task that's still active, return it.
      // Rejected tasks are returned too so the agent can read feedback and fix.
      if (agent.currentTaskId) {
        const current = taskBoard.getTask(agent.currentTaskId);
        if (current && !["accepted", "failed"].includes(current.status)) {
          return {
            content: [{ type: "text" as const, text: JSON.stringify({
              message: current.status === "rejected"
                ? "Your task was rejected. Read the feedback, fix the issues, and resubmit."
                : "You already have an active task",
              task: formatTaskForAgent(current),
              validationFeedback: current.validationFeedback,
            }, null, 2) }],
          };
        }
      }

      const available = taskBoard.getAvailableTasks();
      log.debug(`get_my_task: agent=${agent_id} project=${project.id} available=${available.length} total=${taskBoard.getAllTasks().length}`);
      if (available.length === 0) {
        const allTasks = taskBoard.getAllTasks();
        const terminal = ["accepted", "failed"];
        const allDone = allTasks.length > 0 && allTasks.every(t => terminal.includes(t.status));
        if (allDone) {
          const accepted = allTasks.filter(t => t.status === "accepted").length;
          return { content: [{ type: "text" as const, text: JSON.stringify({
            message: "All tasks are complete. The project is done.",
            done: true,
            accepted,
            total: allTasks.length,
          }) }] };
        }
        const pending = allTasks.filter(t => t.status === "pending").length;
        const inProgress = allTasks.filter(t => ["assigned", "in_progress", "submitted"].includes(t.status)).length;
        return { content: [{ type: "text" as const, text: JSON.stringify({
          message: "No tasks available right now, but the project is NOT done. There are tasks blocked on dependencies being completed by other agents. Please wait 15-30 seconds and call get_my_task again.",
          done: false,
          pendingTasks: pending,
          inProgressTasks: inProgress,
        }) }] };
      }

      const task = available[0];
      const branch = `agent/${agent_id}/${task.id}`;
      const assigned = taskBoard.assignTask(task.id, agent_id, branch);
      if (!assigned) {
        return { content: [{ type: "text" as const, text: JSON.stringify({
          error: "Failed to assign task — likely a race condition. Call get_my_task again to get another task.",
        }) }] };
      }

      // Create branch in the repo — worker will fetch + checkout themselves
      try {
        await gitRepo.createBranch(branch);
        // Push branch to GitHub if configured
        if (project.githubRepoUrl) {
          try {
            await gitRepo.push("origin", branch);
          } catch (err) {
            log.debug(`Failed to push branch to GitHub: ${err}`);
          }
        }
      } catch (err) {
        log.warn(`Failed to create branch ${branch}: ${err}`);
      }

      log.info(`Assigned task ${task.id} to ${agent_id}`);
      return {
        content: [{ type: "text" as const, text: JSON.stringify({
          task: formatTaskForAgent(assigned),
          instructions: "In your clone: git fetch origin && git checkout -B " + branch + " origin/" + branch,
        }, null, 2) }],
      };
    }
  );

  // --- Tool: report_status ---
  server.tool(
    "report_status",
    "Report progress on your current task",
    {
      agent_id: z.string().describe("Your agent ID"),
      task_id: z.string().describe("The task you're reporting on"),
      status: z.enum(["in_progress", "blocked", "needs_help"]).describe("Current status"),
      description: z.string().optional().describe("What you're working on / what's blocking you"),
    },
    async ({ agent_id, task_id, status, description }) => {
      const ctx = recoverSession(agent_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { taskBoard } = ctx;

      const task = taskBoard.getTask(task_id);
      if (!task || task.assignedTo !== agent_id) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found or not assigned to you" }) }] };
      }
      taskBoard.touchTask(task_id);
      if (status === "in_progress") {
        taskBoard.updateTaskStatus(task_id, "in_progress");
      }
      log.info(`Status report from ${agent_id} on ${task_id}: ${status} — ${description || ""}`);
      return { content: [{ type: "text" as const, text: JSON.stringify({ ack: true, message: "Status received" }) }] };
    }
  );

  // --- Tool: check_updates ---
  server.tool(
    "check_updates",
    "Check for spec changes or validation results on your task",
    {
      agent_id: z.string().describe("Your agent ID"),
      task_id: z.string().describe("The task to check"),
    },
    async ({ agent_id, task_id }) => {
      const ctx = recoverSession(agent_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { taskBoard } = ctx;

      const task = taskBoard.getTask(task_id);
      if (!task || task.assignedTo !== agent_id) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found or not assigned to you" }) }] };
      }
      taskBoard.touchTask(task_id);
      const updates: Record<string, unknown> = {
        taskStatus: task.status,
      };
      if (task.validationFeedback) {
        updates.validationFeedback = task.validationFeedback;
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(updates, null, 2) }] };
    }
  );

  // --- Tool: submit_result ---
  server.tool(
    "submit_result",
    "Submit your branch for review — push your branch first, then call this. The orchestrator will validate async.",
    {
      agent_id: z.string().describe("Your agent ID"),
      task_id: z.string().describe("The task you're submitting"),
    },
    async ({ agent_id, task_id }) => {
      const ctx = recoverSession(agent_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { taskBoard, gitRepo, project } = ctx;

      const task = taskBoard.getTask(task_id);
      if (!task || task.assignedTo !== agent_id) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found or not assigned to you" }) }] };
      }
      if (!task.branch) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No branch assigned" }) }] };
      }

      // Rate limit: max 3 submissions per minute per agent
      if (isSubmitRateLimited(agent_id)) {
        log.warn(`Rate limited submit_result from ${agent_id} on ${task_id}`);
        return { content: [{ type: "text" as const, text: JSON.stringify({
          error: "Rate limited. Max 3 submissions per minute. Wait and try again.",
        }) }] };
      }

      taskBoard.touchTask(task_id);

      // When GitHub is configured, workers push to the remote.
      // We must fetch and use origin/<branch> refs since the branch
      // only exists on the remote, not as a local branch.
      const hasRemote = project.githubRepoUrl != null;
      let branchRef = task.branch;

      if (hasRemote) {
        try {
          await gitRepo.fetch("origin");
        } catch (err) {
          log.debug(`Fetch from origin failed: ${err}`);
        }
        branchRef = `origin/${task.branch}`;
      }

      // Branch verification
      const exists = await gitRepo.branchExists(branchRef);
      if (!exists) {
        const feedback = `Branch not found: ${task.branch}. Make sure you pushed your branch to the repo.`;
        taskBoard.updateTaskStatus(task_id, "rejected", feedback);
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: feedback }) }] };
      }

      const hasCommits = await gitRepo.branchHasCommits(branchRef);
      if (!hasCommits) {
        const feedback = `Branch ${task.branch} has no commits ahead of main. Make sure you committed and pushed your changes.`;
        taskBoard.updateTaskStatus(task_id, "rejected", feedback);
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: feedback }) }] };
      }

      // Worker has pushed their branch — diff against it in our repo
      try {
        const diff = await gitRepo.getDiffMergeBase(branchRef, task.spec.filePaths);
        taskBoard.setSubmissionDiff(task_id, diff);
      } catch (err) {
        log.warn(`Could not get diff for ${task.branch}: ${err}`);
      }

      taskBoard.clearValidationFeedback(task_id);
      taskBoard.updateTaskStatus(task_id, "submitted");
      log.info(`Task ${task_id} submitted by ${agent_id}`);

      return {
        content: [{ type: "text" as const, text: JSON.stringify({
          message: "Submission received. The orchestrator will validate your work asynchronously.",
          instruction: "Call check_updates to poll for the result.",
        }, null, 2) }],
      };
    }
  );

  // --- Tool: get_project_context ---
  server.tool(
    "get_project_context",
    "Read files from the main branch",
    {
      file_paths: z.array(z.string()).describe("Paths to read from the main branch"),
    },
    async ({ file_paths }) => {
      const ctx = getProjectCtx();
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { gitRepo } = ctx;

      const results: Record<string, string> = {};
      for (const fp of file_paths) {
        results[fp] = await gitRepo.readFileFromMain(fp);
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(results, null, 2) }] };
    }
  );

  return server;
}

function formatTaskForAgent(task: import("../types/task.js").Task) {
  return {
    id: task.id,
    title: task.spec.title,
    description: task.spec.description,
    instructions: task.spec.instructions,
    filePaths: task.spec.filePaths,
    interfaceContract: task.spec.interfaceContract,
    branch: task.branch,
    status: task.status,
  };
}
