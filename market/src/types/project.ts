import { TaskSpec } from "./task.js";

export interface Project {
  id: string;
  name: string;
  description: string;
  createdAt: Date;
  status: "planning" | "active" | "completed";
}

export interface ProjectPlan {
  projectId: string;
  scaffold: ScaffoldFile[];
  tasks: TaskSpec[];
  sharedTypes: string; // content of shared type definitions
}

export interface ScaffoldFile {
  path: string;
  content: string;
}
