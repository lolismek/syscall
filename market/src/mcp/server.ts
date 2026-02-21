import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { TaskBoard } from "../state/task-board.js";
import { ProjectStore } from "../state/project-store.js";
import { GitRepo } from "../git/repo.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("MCP");

export type McpServerFactory = () => McpServer;

export function createMcpServerFactory(
  taskBoard: TaskBoard,
  projectStore: ProjectStore,
  gitRepo: GitRepo
): McpServerFactory {
  return () => createMcpServer(taskBoard, projectStore, gitRepo);
}

function createMcpServer(
  taskBoard: TaskBoard,
  projectStore: ProjectStore,
  gitRepo: GitRepo
): McpServer {
  const server = new McpServer({
    name: "syscall-orchestrator",
    version: "0.1.0",
  });

  // --- Tool: join_project ---
  server.tool(
    "join_project",
    "Register as a worker agent and get the project summary",
    {
      agent_name: z.string().describe("Your name / identifier"),
      capabilities: z.array(z.string()).describe("What you can do, e.g. ['typescript', 'react']"),
    },
    async ({ agent_name, capabilities }) => {
      const project = projectStore.getProject();
      if (!project) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No active project" }) }] };
      }
      const agent = taskBoard.registerAgent(agent_name, capabilities);
      const tasks = taskBoard.getAllTasks();
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
        rules: [
          "Call get_my_task to receive your assignment",
          "Call report_status when you start working",
          "Call check_updates before submitting",
          "Work on your assigned branch only",
          "Call submit_result when done — do NOT merge to main",
        ],
        workspace: gitRepo.getRepoPath(),
      };
      log.info(`Agent joined: ${agent.name} (${agent.id})`);
      return { content: [{ type: "text" as const, text: JSON.stringify(summary, null, 2) }] };
    }
  );

  // --- Tool: get_my_task ---
  server.tool(
    "get_my_task",
    "Get the next available task assigned to you",
    {
      agent_id: z.string().describe("Your agent ID from join_project"),
    },
    async ({ agent_id }) => {
      const agent = taskBoard.getAgent(agent_id);
      if (!agent) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Unknown agent" }) }] };
      }

      // If agent already has a task, return it
      if (agent.currentTaskId) {
        const current = taskBoard.getTask(agent.currentTaskId);
        if (current && !["accepted", "rejected", "failed"].includes(current.status)) {
          return {
            content: [{ type: "text" as const, text: JSON.stringify({
              message: "You already have an active task",
              task: formatTaskForAgent(current),
            }, null, 2) }],
          };
        }
      }

      const available = taskBoard.getAvailableTasks();
      if (available.length === 0) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ message: "No tasks available right now. Try again later." }) }] };
      }

      const task = available[0];
      const branch = `agent/${agent_id}/${task.id}`;
      const assigned = taskBoard.assignTask(task.id, agent_id, branch);
      if (!assigned) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Failed to assign task" }) }] };
      }

      // Create git branch
      try {
        await gitRepo.createBranch(branch);
      } catch (err) {
        log.warn(`Failed to create branch ${branch}: ${err}`);
      }

      log.info(`Assigned task ${task.id} to ${agent_id}`);
      return {
        content: [{ type: "text" as const, text: JSON.stringify({
          task: formatTaskForAgent(assigned),
          workspace: gitRepo.getRepoPath(),
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
      const task = taskBoard.getTask(task_id);
      if (!task || task.assignedTo !== agent_id) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found or not assigned to you" }) }] };
      }
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
      const task = taskBoard.getTask(task_id);
      if (!task || task.assignedTo !== agent_id) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found or not assigned to you" }) }] };
      }
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
    "Submit your branch for review — the orchestrator will validate async",
    {
      agent_id: z.string().describe("Your agent ID"),
      task_id: z.string().describe("The task you're submitting"),
    },
    async ({ agent_id, task_id }) => {
      const task = taskBoard.getTask(task_id);
      if (!task || task.assignedTo !== agent_id) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "Task not found or not assigned to you" }) }] };
      }
      if (!task.branch) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ error: "No branch assigned" }) }] };
      }

      // Capture diff BEFORE any merge — use merge-base so it's always correct
      try {
        const diff = await gitRepo.getDiffMergeBase(task.branch);
        taskBoard.setSubmissionDiff(task_id, diff);
      } catch (err) {
        log.warn(`Could not get diff for ${task.branch}: ${err}`);
      }

      // Clear stale validation feedback from previous rounds
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
