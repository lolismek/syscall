export type TaskStatus =
  | "pending"
  | "assigned"
  | "in_progress"
  | "submitted"
  | "accepted"
  | "rejected"
  | "failed";

export interface TaskSpec {
  title: string;
  description: string;
  instructions: string;
  filePaths: string[];       // files this task should create/modify
  dependencies: string[];    // task IDs that must complete first
  interfaceContract: string; // what the output must conform to
}

export interface Task {
  id: string;
  projectId: string;
  spec: TaskSpec;
  status: TaskStatus;
  assignedTo: string | null; // agent ID
  branch: string | null;
  submissionDiff: string | null;
  validationFeedback: string | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface TaskUpdate {
  taskId: string;
  status: TaskStatus;
  description?: string;
}
