import path from "path";

export const config = {
  port: parseInt(process.env.PORT || "3100", 10),
  workspacePath: process.env.WORKSPACE_PATH || path.resolve(process.cwd(), "workspace"),
  model: process.env.MODEL || "claude-4-sonnet-20250514",
  maxConcurrentAgents: 10,
};
