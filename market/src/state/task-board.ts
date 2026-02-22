import { EventEmitter } from "events";
import type { Database } from "bun:sqlite";
import { Task, TaskSpec, TaskStatus } from "../types/task.js";
import { AgentInfo } from "../types/agent.js";
import { Project } from "../types/project.js";
import { createLogger } from "../utils/logger.js";

const log = createLogger("TaskBoard");

// Helper: row from DB → Task object
function rowToTask(row: any): Task {
  return {
    id: row.id,
    projectId: row.project_id,
    spec: {
      title: row.title,
      description: row.description,
      instructions: row.instructions,
      filePaths: JSON.parse(row.file_paths),
      dependencies: JSON.parse(row.dependencies),
      interfaceContract: row.interface_contract,
    },
    status: row.status as TaskStatus,
    assignedTo: row.assigned_to,
    branch: row.branch,
    submissionDiff: row.submission_diff,
    validationFeedback: row.validation_feedback,
    lastActivityAt: new Date(row.last_activity_at),
    createdAt: new Date(row.created_at),
    updatedAt: new Date(row.updated_at),
  };
}

function rowToAgent(row: any): AgentInfo {
  return {
    id: row.id,
    name: row.name,
    capabilities: JSON.parse(row.capabilities),
    joinedAt: new Date(row.joined_at),
    currentTaskId: row.current_task_id,
    projectId: row.project_id,
  };
}

export class TaskBoard extends EventEmitter {
  private db: Database;
  private projectId: string;
  private projectShortId: string | null = null;

  constructor(db: Database, projectId: string) {
    super();
    this.db = db;
    this.projectId = projectId;
  }

  setProjectShortId(shortId: string): void {
    this.projectShortId = shortId;
    // Also persist to projects table
    this.db.run("UPDATE projects SET short_id = ? WHERE id = ?", [shortId, this.projectId]);
  }

  setProject(project: Project): void {
    // Upsert into projects table
    this.db.run(
      `INSERT OR REPLACE INTO projects (id, name, description, created_at, ready_at, recruiting_until, min_agents, status, github_repo_url, github_repo_name, nia_repo_id, nia_source_ids, next_task_num, short_id)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT next_task_num FROM projects WHERE id = ?), 1), COALESCE((SELECT short_id FROM projects WHERE id = ?), ?))`,
      [
        project.id,
        project.name,
        project.description,
        project.createdAt.toISOString(),
        project.readyAt.toISOString(),
        project.recruitingUntil ? project.recruitingUntil.toISOString() : null,
        project.minAgents,
        project.status,
        project.githubRepoUrl,
        project.githubRepoName,
        project.niaRepoId ?? null,
        project.niaSourceIds ? JSON.stringify(project.niaSourceIds) : "[]",
        project.id,
        project.id,
        this.projectShortId,
      ],
    );
  }

  getProject(): Project | null {
    const row = this.db.query("SELECT * FROM projects WHERE id = ?").get(this.projectId) as any;
    if (!row) return null;
    return {
      id: row.id,
      name: row.name,
      description: row.description,
      createdAt: new Date(row.created_at),
      readyAt: new Date(row.ready_at),
      recruitingUntil: row.recruiting_until ? new Date(row.recruiting_until) : null,
      minAgents: row.min_agents,
      status: row.status,
      githubRepoUrl: row.github_repo_url,
      githubRepoName: row.github_repo_name,
      niaRepoId: row.nia_repo_id ?? undefined,
      niaSourceIds: row.nia_source_ids ? JSON.parse(row.nia_source_ids) : undefined,
    };
  }

  // --- Task operations ---

  addTask(projectId: string, spec: TaskSpec): Task {
    // Get and increment next_task_num
    const projRow = this.db.query("SELECT next_task_num, short_id FROM projects WHERE id = ?").get(projectId) as any;
    const num = projRow?.next_task_num ?? 1;
    const shortId = projRow?.short_id ?? this.projectShortId;
    const prefix = shortId ? `proj-${shortId}-task` : "task";
    const id = `${prefix}-${String(num).padStart(3, "0")}`;

    this.db.run("UPDATE projects SET next_task_num = ? WHERE id = ?", [num + 1, projectId]);

    const now = new Date().toISOString();
    this.db.run(
      `INSERT INTO tasks (id, project_id, title, description, instructions, file_paths, dependencies, interface_contract, status, assigned_to, branch, submission_diff, validation_feedback, last_activity_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL, ?, ?, ?)`,
      [
        id, projectId, spec.title, spec.description, spec.instructions,
        JSON.stringify(spec.filePaths), JSON.stringify(spec.dependencies), spec.interfaceContract,
        now, now, now,
      ],
    );

    log.info(`Task added: ${id} — ${spec.title}`);
    return this.getTask(id)!;
  }

  getTask(taskId: string): Task | undefined {
    const row = this.db.query("SELECT * FROM tasks WHERE id = ?").get(taskId) as any;
    return row ? rowToTask(row) : undefined;
  }

  getAllTasks(): Task[] {
    const rows = this.db.query("SELECT * FROM tasks WHERE project_id = ?").all(this.projectId) as any[];
    return rows.map(rowToTask);
  }

  getAvailableTasks(): Task[] {
    const pending = this.db.query("SELECT * FROM tasks WHERE project_id = ? AND status = 'pending'").all(this.projectId) as any[];
    return pending.map(rowToTask).filter((t) => {
      return t.spec.dependencies.every((depId) => {
        const dep = this.getTask(depId);
        return dep && dep.status === "accepted";
      });
    });
  }

  setTaskDependencies(taskId: string, dependencies: string[]): void {
    const now = new Date().toISOString();
    this.db.run(
      "UPDATE tasks SET dependencies = ?, updated_at = ? WHERE id = ?",
      [JSON.stringify(dependencies), now, taskId],
    );
  }

  assignTask(taskId: string, agentId: string, branch: string): Task | null {
    const task = this.getTask(taskId);
    if (!task || task.status !== "pending") return null;

    const now = new Date().toISOString();
    this.db.run(
      "UPDATE tasks SET status = 'assigned', assigned_to = ?, branch = ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
      [agentId, branch, now, now, taskId],
    );
    this.db.run("UPDATE agents SET current_task_id = ? WHERE id = ?", [taskId, agentId]);

    log.info(`Task ${taskId} assigned to agent ${agentId} on branch ${branch}`);
    return this.getTask(taskId)!;
  }

  updateTaskStatus(taskId: string, status: TaskStatus, feedback?: string): Task | null {
    const task = this.getTask(taskId);
    if (!task) return null;

    const now = new Date().toISOString();
    if (feedback !== undefined) {
      this.db.run(
        "UPDATE tasks SET status = ?, validation_feedback = ?, updated_at = ? WHERE id = ?",
        [status, feedback, now, taskId],
      );
    } else {
      this.db.run(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        [status, now, taskId],
      );
    }

    log.info(`Task ${taskId} status → ${status}`);

    if (status === "submitted") {
      this.emit("task_submitted", this.getTask(taskId)!);
    }

    return this.getTask(taskId)!;
  }

  clearValidationFeedback(taskId: string): void {
    const now = new Date().toISOString();
    this.db.run("UPDATE tasks SET validation_feedback = NULL, updated_at = ? WHERE id = ?", [now, taskId]);
  }

  setSubmissionDiff(taskId: string, diff: string): void {
    const now = new Date().toISOString();
    this.db.run("UPDATE tasks SET submission_diff = ?, updated_at = ? WHERE id = ?", [diff, now, taskId]);
  }

  // --- Activity tracking (for timeout) ---

  touchTask(taskId: string): void {
    const now = new Date().toISOString();
    this.db.run("UPDATE tasks SET last_activity_at = ? WHERE id = ?", [now, taskId]);
  }

  getTimedOutTasks(timeoutMs: number): Task[] {
    const cutoff = new Date(Date.now() - timeoutMs).toISOString();
    const rows = this.db.query(
      "SELECT * FROM tasks WHERE project_id = ? AND status IN ('assigned', 'in_progress') AND last_activity_at < ?",
    ).all(this.projectId, cutoff) as any[];
    return rows.map(rowToTask);
  }

  reassignTask(taskId: string): void {
    const task = this.getTask(taskId);
    if (!task) return;

    const oldAgent = task.assignedTo;
    if (oldAgent) {
      const agent = this.getAgent(oldAgent);
      if (agent && agent.currentTaskId === taskId) {
        this.db.run("UPDATE agents SET current_task_id = NULL WHERE id = ?", [oldAgent]);
      }
    }

    const now = new Date().toISOString();
    this.db.run(
      "UPDATE tasks SET status = 'pending', assigned_to = NULL, branch = NULL, updated_at = ?, last_activity_at = ? WHERE id = ?",
      [now, now, taskId],
    );

    log.warn(`Task ${taskId} reassigned to pool (agent ${oldAgent} timed out)`);
  }

  // --- Agent operations ---

  registerAgent(name: string, capabilities: string[], projectId?: string): AgentInfo {
    const id = `agent-${name.toLowerCase().replace(/\s+/g, "-")}-${Date.now().toString(36)}`;
    const pid = projectId || this.projectId;
    const now = new Date().toISOString();

    this.db.run(
      "INSERT INTO agents (id, name, capabilities, joined_at, current_task_id, project_id) VALUES (?, ?, ?, ?, NULL, ?)",
      [id, name, JSON.stringify(capabilities), now, pid],
    );

    log.info(`Agent registered: ${id} (${name})`);
    return this.getAgent(id)!;
  }

  getAgent(agentId: string): AgentInfo | undefined {
    const row = this.db.query("SELECT * FROM agents WHERE id = ?").get(agentId) as any;
    return row ? rowToAgent(row) : undefined;
  }

  getAllAgents(): AgentInfo[] {
    const rows = this.db.query("SELECT * FROM agents WHERE project_id = ?").all(this.projectId) as any[];
    return rows.map(rowToAgent);
  }

  // --- Cycle detection ---

  hasCyclicDependencies(): { hasCycle: boolean; cycle?: string[] } {
    const tasks = this.getAllTasks();
    const taskMap = new Map(tasks.map((t) => [t.id, t]));

    const WHITE = 0, GRAY = 1, BLACK = 2;
    const color = new Map<string, number>();
    const parent = new Map<string, string | null>();

    for (const t of tasks) color.set(t.id, WHITE);

    const dfs = (u: string): string[] | null => {
      color.set(u, GRAY);
      const task = taskMap.get(u);
      if (!task) return null;

      for (const dep of task.spec.dependencies) {
        if (!taskMap.has(dep)) continue;
        if (color.get(dep) === GRAY) {
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

    for (const t of tasks) {
      if (color.get(t.id) === WHITE) {
        parent.set(t.id, null);
        const cycle = dfs(t.id);
        if (cycle) return { hasCycle: true, cycle };
      }
    }

    return { hasCycle: false };
  }
}
