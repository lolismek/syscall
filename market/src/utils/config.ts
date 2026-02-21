import path from "path";

export const config = {
  port: parseInt(process.env.PORT || "3100", 10),
  workspacePath: process.env.WORKSPACE_PATH || path.resolve(process.cwd(), "workspace"),
  model: process.env.MODEL || "claude-4-sonnet-20250514",
  maxConcurrentAgents: 10,
  taskTimeoutMs: parseInt(process.env.TASK_TIMEOUT_MS || String(15 * 60 * 1000), 10),
  agentWaitMs: parseInt(process.env.AGENT_WAIT_MS || "0", 10),
  agentApiKey: process.env.AGENT_API_KEY || null,
  githubOrg: process.env.GITHUB_ORG || null,
  githubToken: process.env.GITHUB_TOKEN || null,
};
