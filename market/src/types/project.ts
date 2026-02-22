import { TaskSpec } from "./task.js";

export interface Project {
  id: string;
  name: string;
  description: string;
  createdAt: Date;
  readyAt: Date;
  recruitingUntil: Date | null;
  minAgents: number;
  status: "planning" | "recruiting" | "active" | "completed" | "stopped";
  githubRepoUrl: string | null;
  githubRepoName: string | null;
  niaRepoId?: string;
  niaSourceIds?: string[];
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
