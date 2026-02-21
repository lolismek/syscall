import { EventEmitter } from "events";
import { Task, TaskSpec, TaskStatus } from "../types/task.js";
import { AgentInfo } from "../types/agent.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("TaskBoard");

export class TaskBoard extends EventEmitter {
  private tasks: Map<string, Task> = new Map();
  private agents: Map<string, AgentInfo> = new Map();
  private nextTaskNum = 1;

  // --- Task operations ---

  addTask(projectId: string, spec: TaskSpec): Task {
    const id = `task-${String(this.nextTaskNum++).padStart(3, "0")}`;
    const task: Task = {
      id,
      projectId,
      spec,
      status: "pending",
      assignedTo: null,
      branch: null,
      submissionDiff: null,
      validationFeedback: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    this.tasks.set(id, task);
    log.info(`Task added: ${id} — ${spec.title}`);
    return task;
  }

  getTask(taskId: string): Task | undefined {
    return this.tasks.get(taskId);
  }

  getAllTasks(): Task[] {
    return Array.from(this.tasks.values());
  }

  getAvailableTasks(): Task[] {
    return this.getAllTasks().filter((t) => {
      if (t.status !== "pending") return false;
      // Check dependencies are all accepted
      return t.spec.dependencies.every((depId) => {
        const dep = this.tasks.get(depId);
        return dep && dep.status === "accepted";
      });
    });
  }

  assignTask(taskId: string, agentId: string, branch: string): Task | null {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== "pending") return null;
    task.status = "assigned";
    task.assignedTo = agentId;
    task.branch = branch;
    task.updatedAt = new Date();

    // Update agent's current task
    const agent = this.agents.get(agentId);
    if (agent) agent.currentTaskId = taskId;

    log.info(`Task ${taskId} assigned to agent ${agentId} on branch ${branch}`);
    return task;
  }

  updateTaskStatus(taskId: string, status: TaskStatus, feedback?: string): Task | null {
    const task = this.tasks.get(taskId);
    if (!task) return null;
    task.status = status;
    task.updatedAt = new Date();
    if (feedback !== undefined) {
      task.validationFeedback = feedback;
    }
    log.info(`Task ${taskId} status → ${status}`);

    if (status === "submitted") {
      this.emit("task_submitted", task);
    }

    return task;
  }

  clearValidationFeedback(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.validationFeedback = null;
      task.updatedAt = new Date();
    }
  }

  setSubmissionDiff(taskId: string, diff: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.submissionDiff = diff;
      task.updatedAt = new Date();
    }
  }

  // --- Agent operations ---

  registerAgent(name: string, capabilities: string[]): AgentInfo {
    const id = `agent-${name.toLowerCase().replace(/\s+/g, "-")}-${Date.now().toString(36)}`;
    const agent: AgentInfo = {
      id,
      name,
      capabilities,
      joinedAt: new Date(),
      currentTaskId: null,
    };
    this.agents.set(id, agent);
    log.info(`Agent registered: ${id} (${name})`);
    return agent;
  }

  getAgent(agentId: string): AgentInfo | undefined {
    return this.agents.get(agentId);
  }

  getAllAgents(): AgentInfo[] {
    return Array.from(this.agents.values());
  }
}
