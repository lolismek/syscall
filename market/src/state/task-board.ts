import { EventEmitter } from "events";
import fs from "fs/promises";
import path from "path";
import { Task, TaskSpec, TaskStatus } from "../types/task.js";
import { AgentInfo } from "../types/agent.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("TaskBoard");

export class TaskBoard extends EventEmitter {
  private tasks: Map<string, Task> = new Map();
  private agents: Map<string, AgentInfo> = new Map();
  private nextTaskNum = 1;
  private savePath: string | null = null;
  private saveQueued = false;
  private saving = false;

  setSavePath(savePath: string): void {
    this.savePath = savePath;
  }

  // --- Persistence ---

  private save(): void {
    if (!this.savePath) return;
    // Coalesce rapid saves: if already saving, just mark dirty
    if (this.saving) {
      this.saveQueued = true;
      return;
    }
    this._doSave();
  }

  private async _doSave(): Promise<void> {
    if (!this.savePath) return;
    this.saving = true;
    try {
      const data = {
        tasks: Array.from(this.tasks.entries()),
        agents: Array.from(this.agents.entries()),
        nextTaskNum: this.nextTaskNum,
      };
      const dir = path.dirname(this.savePath);
      await fs.mkdir(dir, { recursive: true });
      const tmp = this.savePath + ".tmp";
      await fs.writeFile(tmp, JSON.stringify(data, null, 2));
      await fs.rename(tmp, this.savePath);
    } catch (err) {
      log.warn(`Failed to save state: ${err}`);
    } finally {
      this.saving = false;
      if (this.saveQueued) {
        this.saveQueued = false;
        this._doSave();
      }
    }
  }

  static async load(filePath: string): Promise<{
    tasks: [string, Task][];
    agents: [string, AgentInfo][];
    nextTaskNum: number;
  } | null> {
    try {
      const raw = await fs.readFile(filePath, "utf-8");
      const data = JSON.parse(raw);
      // Rehydrate Date fields on tasks
      for (const [, task] of data.tasks) {
        task.createdAt = new Date(task.createdAt);
        task.updatedAt = new Date(task.updatedAt);
        task.lastActivityAt = task.lastActivityAt ? new Date(task.lastActivityAt) : task.updatedAt;
      }
      // Rehydrate Date fields on agents
      for (const [, agent] of data.agents) {
        agent.joinedAt = new Date(agent.joinedAt);
      }
      return data;
    } catch {
      return null;
    }
  }

  hydrate(data: {
    tasks: [string, Task][];
    agents: [string, AgentInfo][];
    nextTaskNum: number;
  }): void {
    this.tasks = new Map(data.tasks);
    this.agents = new Map(data.agents);
    this.nextTaskNum = data.nextTaskNum;
    log.info(`Hydrated: ${this.tasks.size} tasks, ${this.agents.size} agents`);
  }

  // --- Task operations ---

  addTask(projectId: string, spec: TaskSpec): Task {
    const id = `task-${String(this.nextTaskNum++).padStart(3, "0")}`;
    const now = new Date();
    const task: Task = {
      id,
      projectId,
      spec,
      status: "pending",
      assignedTo: null,
      branch: null,
      submissionDiff: null,
      validationFeedback: null,
      lastActivityAt: now,
      createdAt: now,
      updatedAt: now,
    };
    this.tasks.set(id, task);
    log.info(`Task added: ${id} — ${spec.title}`);
    this.save();
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
    const now = new Date();
    task.status = "assigned";
    task.assignedTo = agentId;
    task.branch = branch;
    task.lastActivityAt = now;
    task.updatedAt = now;

    // Update agent's current task
    const agent = this.agents.get(agentId);
    if (agent) agent.currentTaskId = taskId;

    log.info(`Task ${taskId} assigned to agent ${agentId} on branch ${branch}`);
    this.save();
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

    this.save();
    return task;
  }

  clearValidationFeedback(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.validationFeedback = null;
      task.updatedAt = new Date();
      this.save();
    }
  }

  setSubmissionDiff(taskId: string, diff: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.submissionDiff = diff;
      task.updatedAt = new Date();
      this.save();
    }
  }

  // --- Activity tracking (for timeout) ---

  touchTask(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.lastActivityAt = new Date();
      // No save() here — too frequent, save is called by the mutating methods
    }
  }

  getTimedOutTasks(timeoutMs: number): Task[] {
    const now = Date.now();
    return this.getAllTasks().filter((t) => {
      if (t.status !== "assigned" && t.status !== "in_progress") return false;
      return now - t.lastActivityAt.getTime() > timeoutMs;
    });
  }

  reassignTask(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (!task) return;

    const oldAgent = task.assignedTo;

    // Clear agent's currentTaskId
    if (oldAgent) {
      const agent = this.agents.get(oldAgent);
      if (agent && agent.currentTaskId === taskId) {
        agent.currentTaskId = null;
      }
    }

    task.status = "pending";
    task.assignedTo = null;
    task.branch = null;
    task.updatedAt = new Date();
    task.lastActivityAt = new Date();

    log.warn(`Task ${taskId} reassigned to pool (agent ${oldAgent} timed out)`);
    this.save();
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
    this.save();
    return agent;
  }

  getAgent(agentId: string): AgentInfo | undefined {
    return this.agents.get(agentId);
  }

  getAllAgents(): AgentInfo[] {
    return Array.from(this.agents.values());
  }

  // --- Cycle detection ---

  hasCyclicDependencies(): { hasCycle: boolean; cycle?: string[] } {
    const WHITE = 0, GRAY = 1, BLACK = 2;
    const color = new Map<string, number>();
    const parent = new Map<string, string | null>();

    for (const id of this.tasks.keys()) {
      color.set(id, WHITE);
    }

    const dfs = (u: string): string[] | null => {
      color.set(u, GRAY);
      const task = this.tasks.get(u);
      if (!task) return null;

      for (const dep of task.spec.dependencies) {
        if (!this.tasks.has(dep)) continue;
        if (color.get(dep) === GRAY) {
          // Found cycle — reconstruct
          const cycle = [dep, u];
          let cur = u;
          while (cur !== dep) {
            cur = parent.get(cur)!;
            if (!cur || cur === dep) break;
            cycle.push(cur);
          }
          cycle.push(dep);
          return cycle.reverse();
        }
        if (color.get(dep) === WHITE) {
          parent.set(dep, u);
          const result = dfs(dep);
          if (result) return result;
        }
      }

      color.set(u, BLACK);
      return null;
    };

    for (const id of this.tasks.keys()) {
      if (color.get(id) === WHITE) {
        parent.set(id, null);
        const cycle = dfs(id);
        if (cycle) return { hasCycle: true, cycle };
      }
    }

    return { hasCycle: false };
  }
}
