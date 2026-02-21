export interface AgentInfo {
  id: string;
  name: string;
  capabilities: string[];
  joinedAt: Date;
  currentTaskId: string | null;
  projectId: string;
}
