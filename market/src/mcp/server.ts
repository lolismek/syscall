import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { ProjectRegistry } from "../state/project-registry.js";
import { createLogger } from "../utils/logger.js";
import { config } from "../utils/config.js";
import { NiaClient } from "../knowledge/nia-client.js";

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

// Agent name pool — server assigns unique names so agents don't collide
const AGENT_NAMES = [
  "alice", "bob", "carol", "dave", "eve", "frank", "grace", "heidi",
  "ivan", "judy", "karl", "lana", "mike", "nina", "oscar", "pam",
  "quinn", "rose", "steve", "tina", "uma", "vic", "wendy", "xander",
  "yuki", "zara",
];

function pickAgentName(registry: ProjectRegistry): string {
  // Collect all names already in use across all projects
  const usedNames = new Set<string>();
  for (const ctx of registry.list()) {
    for (const agent of ctx.taskBoard.getAllAgents()) {
      usedNames.add(agent.name.toLowerCase());
    }
  }
  // Pick the first unused name
  for (const name of AGENT_NAMES) {
    if (!usedNames.has(name)) return name;
  }
  // Fallback: random suffix
  return "agent-" + Math.random().toString(36).slice(2, 6);
}

function getServerInstructions(registry: ProjectRegistry): string {
  // Include live project info so the agent can skip list_projects when there's only one
  const projects = registry.list().filter(ctx => ctx.project.status !== "stopped");
  let projectHint = "";
  if (projects.length === 1) {
    const p = projects[0];
    projectHint = `\nThere is currently one active project: "${p.project.name}" (id: ${p.project.id}). You can join it directly.`;
  } else if (projects.length > 1) {
    projectHint = "\nThere are " + projects.length + " active projects. Call list_projects first to pick one.";
  } else {
    projectHint = "\nNo projects are active yet. Wait for a project to be created, then call list_projects.";
  }

  return `You are a worker agent for the Syscall Market orchestrator. You have MCP tools connected to the orchestrator.
${projectHint}

Follow this exact workflow:

1. Call join_project with the project_id and capabilities ["typescript", "general"]. You can omit agent_name — the server assigns a unique name automatically.
2. Note your agentId and repoUrl from the response.
3. Clone the repo: git clone <repoUrl> repo
4. Call get_my_task with your agent_id to get your assignment.
5. Call report_status with status "in_progress".
6. Fetch and checkout the assigned branch: cd repo && git fetch origin && git checkout -B <branch> origin/<branch>
   (If the remote branch doesn't exist yet, just: cd repo && git checkout -b <branch>)
7. Read the task instructions carefully. Implement the code in the specified filePaths. Write real, working TypeScript code — not placeholders. Only create/modify files listed in your task's filePaths.
8. Use get_project_context to read any scaffold files on main that your task depends on (e.g. shared types, package.json).
9. If your code imports from modules created by other tasks, assume they exist — just write correct import statements.
10. cd repo && git add . && git commit -m "task-XXX: description"
11. cd repo && git push origin <your-assigned-branch>
12. Call submit_result with your agent_id and task_id.
13. Poll check_updates every 10 seconds until status is "accepted" or "rejected".
14. If rejected, read the feedback, fix the issue in repo/, commit, push, and resubmit.
15. Once accepted, call get_my_task again for the next task. Fetch and checkout the new branch. Repeat.

CRITICAL RULES:
- FILE PATHS: The task spec gives paths like "src/data/todoStore.ts" — these are relative to the REPO ROOT. When creating or editing files with your tools, you MUST prepend "repo/" to every path. For example, if the task says filePath "src/data/todoStore.ts", write to "repo/src/data/todoStore.ts". If you write files outside the repo/ directory, git will not see them and your submission will fail.
- Your working repo is the "repo" subdirectory of your cwd. ALWAYS prefix shell commands with "cd repo && ...".
- Work ONLY on your assigned branch. Never commit to main.
- Only create/modify files listed in your task's filePaths. Do not recreate files owned by other tasks.
- Write complete, functional code — not stubs or placeholders.
- After committing, you MUST push before calling submit_result.
- Use get_project_context to read scaffold files and shared types from main when you need context.
- Use search_docs to search for documentation and code patterns.
- NEVER stop until get_my_task returns done: true. If it says "No tasks available" but done is false, it means tasks are blocked waiting on other agents. Wait 20 seconds, then call get_my_task again. Keep retrying — do NOT quit.
- Only stop working when the response contains "done": true, meaning all project tasks are complete.`;
}

function createMcpServer(
  registry: ProjectRegistry,
  sessionCtx: SessionContext,
): McpServer {
  const server = new McpServer({
    name: "syscall-orchestrator",
    version: "0.1.0",
  }, {
    instructions: getServerInstructions(registry),
  });

  /** Helper: get the bound project context or return null (null if stopped) */
  function getProjectCtx() {
    if (!sessionCtx.projectId) return null;
    const ctx = registry.get(sessionCtx.projectId) ?? null;
    if (ctx && ctx.project.status === "stopped") return null;
    return ctx;
  }

  /** Try to recover session by looking up which project owns this agent (null if stopped) */
  function recoverSession(agentId: string): ReturnType<typeof getProjectCtx> {
    if (getProjectCtx()) return getProjectCtx();
    for (const pctx of registry.list()) {
      if (pctx.project.status === "stopped") continue;
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
      agent_name: z.string().optional().describe("Your name (optional — server auto-assigns a unique name if omitted)"),
      capabilities: z.array(z.string()).describe("What you can do, e.g. ['typescript', 'react']"),
    },
    async ({ project_id, agent_name, capabilities }) => {
      // Auto-assign a unique name if not provided
      const resolvedName = agent_name || pickAgentName(registry);
      const ctx = registry.get(project_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Project not found: ${project_id}` }) }] };
      }
      if (ctx.project.status === "stopped") {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: `Project ${project_id} has been stopped. No new agents can join.` }) }] };
      }

      const { project, taskBoard, gitRepo } = ctx;
      const agent = taskBoard.registerAgent(resolvedName, capabilities, project_id);

      // Bind session to this project
      sessionCtx.projectId = project_id;
      sessionCtx.agentId = agent.id;

      // Check if minAgents threshold reached → early transition from recruiting to active
      if (project.status === "recruiting") {
        const agentCount = taskBoard.getAllAgents().length;
        if (agentCount >= project.minAgents) {
          project.status = "active";
          project.recruitingUntil = null;
          project.readyAt = new Date();
          taskBoard.setProject(project);
          log.info(`minAgents (${project.minAgents}) reached — project ${project_id} → active`);
        }
      }

      const tasks = taskBoard.getAllTasks();
      const repoUrl = project.githubRepoUrl || gitRepo.getRepoPath();

      const summary: Record<string, unknown> = {
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
          "Use search_docs with scope 'project' to explore the codebase and find relevant patterns, scope 'general' to search indexed public knowledge (packages, docs, popular repos), or scope 'web' for a broad web search",
          "Use get_project_context to read specific files by exact path — this is always up-to-date and should be preferred when you know which file you need",
          "Note: search_docs project results may be slightly behind the latest code. For exact current file contents, always use get_project_context",
        ],
      };

      // If still recruiting, tell the agent to wait
      if (project.status === "recruiting" && project.recruitingUntil) {
        const remainingMs = new Date(project.recruitingUntil).getTime() - Date.now();
        summary.recruiting = {
          message: "Project is still recruiting agents. Task assignment will begin soon.",
          recruitingUntil: project.recruitingUntil,
          remainingSeconds: Math.max(0, Math.ceil(remainingMs / 1000)),
          connectedAgents: taskBoard.getAllAgents().length,
          minAgents: project.minAgents,
        };
      }

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

      // Check if project is still recruiting
      if (project.status === "recruiting") {
        const recruitingUntil = project.recruitingUntil ? new Date(project.recruitingUntil).getTime() : 0;
        const remainingMs = Math.max(0, recruitingUntil - Date.now());
        const remainingSec = Math.ceil(remainingMs / 1000);
        return {
          content: [{ type: "text" as const, text: JSON.stringify({
            message: `Project is still recruiting agents. Task assignment begins in ${remainingSec}s. Call get_my_task again after that.`,
            status: "recruiting",
            recruitingUntil: project.recruitingUntil,
            remainingSeconds: remainingSec,
            connectedAgents: taskBoard.getAllAgents().length,
            minAgents: project.minAgents,
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
          instructions: "In your clone: git fetch origin && git checkout -B " + branch + " origin/" + branch +
            ". TIP: Use search_docs to explore library docs and find relevant patterns. Use get_project_context to read specific files by path (always up-to-date).",
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

  // --- Tool: search_docs ---
  server.tool(
    "search_docs",
    "Search for code and documentation. Scope 'project': search this project's codebase and dependency docs. Scope 'general': search all of Nia's indexed public knowledge (packages, popular repos, docs). Scope 'web': search the web for anything.",
    {
      agent_id: z.string().describe("Your agent ID"),
      query: z.string().describe("What to search for"),
      scope: z.enum(["project", "general", "web"]).default("project").describe("'project' = this repo + deps, 'general' = all indexed public knowledge, 'web' = web search"),
    },
    async ({ agent_id, query, scope }) => {
      if (!config.niaApiKey) {
        return { content: [{ type: "text" as const, text: "Documentation search not configured. Use get_project_context to read files by path instead." }] };
      }
      const ctx = recoverSession(agent_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { project, taskBoard } = ctx;

      // Touch task activity timer
      const agentRecord = taskBoard.getAgent(agent_id);
      if (agentRecord?.currentTaskId) {
        taskBoard.touchTask(agentRecord.currentTaskId);
      }

      const nia = new NiaClient(config.niaApiKey, agent_id, project.id);
      try {
        if (scope === "project") {
          // Scoped to THIS project's repo + its dependency docs only
          const repos = project.niaRepoId ? [project.niaRepoId] : undefined;
          const sources = project.niaSourceIds?.length ? project.niaSourceIds : undefined;
          const results = await nia.search(query, {
            repositories: repos,
            data_sources: sources,
          });
          if (!results || results.trim() === "" || results === "{}") {
            return { content: [{ type: "text" as const, text: "No results found (index may still be building). Use get_project_context to read files by path instead." }] };
          }
          return { content: [{ type: "text" as const, text: results }] };
        } else if (scope === "general") {
          // Universal search across all of Nia's indexed public knowledge
          // (npm packages, popular repos, documentation sites, etc.)
          const results = await nia.search(query);
          if (!results || results.trim() === "" || results === "{}") {
            return { content: [{ type: "text" as const, text: "No results found. Try scope 'web' for a broader web search." }] };
          }
          return { content: [{ type: "text" as const, text: results }] };
        } else {
          // Pure web search
          const results = await nia.webSearch(query);
          return { content: [{ type: "text" as const, text: results }] };
        }
      } catch (err) {
        log.warn(`search_docs failed for agent ${agent_id}: ${err}`);
        return { content: [{ type: "text" as const, text: "Search unavailable. Use get_project_context to read files by path instead." }] };
      }
    }
  );

  // --- Tool: lookup_docs ---
  server.tool(
    "lookup_docs",
    "Read a file from the indexed project codebase or documentation via semantic lookup",
    {
      agent_id: z.string().describe("Your agent ID"),
      query: z.string().describe("Describe the file or content you're looking for"),
    },
    async ({ agent_id, query }) => {
      if (!config.niaApiKey) {
        return { content: [{ type: "text" as const, text: "Documentation lookup not configured. Use get_project_context to read files by path instead." }] };
      }
      const ctx = recoverSession(agent_id);
      if (!ctx) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No project bound. Call join_project first." }) }] };
      }
      const { project, taskBoard } = ctx;

      const agentRecord = taskBoard.getAgent(agent_id);
      if (agentRecord?.currentTaskId) {
        taskBoard.touchTask(agentRecord.currentTaskId);
      }

      const nia = new NiaClient(config.niaApiKey, agent_id, project.id);
      try {
        // Use scoped search to find and return relevant content
        const repos = project.niaRepoId ? [project.niaRepoId] : undefined;
        const sources = project.niaSourceIds?.length ? project.niaSourceIds : undefined;
        const results = await nia.search(query, {
          repositories: repos,
          data_sources: sources,
        });
        if (!results || results.trim() === "" || results === "{}") {
          return { content: [{ type: "text" as const, text: "No results found (index may still be building). Use get_project_context to read files by path instead." }] };
        }
        return { content: [{ type: "text" as const, text: results }] };
      } catch (err) {
        log.warn(`lookup_docs failed for agent ${agent_id}: ${err}`);
        return { content: [{ type: "text" as const, text: "Lookup unavailable. Use get_project_context to read files by path instead." }] };
      }
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
