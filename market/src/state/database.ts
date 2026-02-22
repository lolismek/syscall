import { Database } from "bun:sqlite";
import path from "path";
import fs from "fs";
import { createLogger } from "../utils/logger.js";

const log = createLogger("Database");

let db: Database | null = null;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  created_at TEXT NOT NULL,
  ready_at TEXT NOT NULL,
  recruiting_until TEXT,
  min_agents INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  github_repo_url TEXT,
  github_repo_name TEXT,
  nia_repo_id TEXT,
  nia_source_ids TEXT,
  next_task_num INTEGER NOT NULL DEFAULT 1,
  short_id TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  instructions TEXT NOT NULL,
  file_paths TEXT NOT NULL,
  dependencies TEXT NOT NULL,
  interface_contract TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  assigned_to TEXT,
  branch TEXT,
  submission_diff TEXT,
  validation_feedback TEXT,
  last_activity_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  capabilities TEXT NOT NULL,
  joined_at TEXT NOT NULL,
  current_task_id TEXT,
  project_id TEXT NOT NULL REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS nia_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT REFERENCES projects(id),
  timestamp TEXT NOT NULL,
  type TEXT NOT NULL,
  source TEXT NOT NULL,
  detail TEXT NOT NULL,
  agent_id TEXT,
  status TEXT NOT NULL,
  duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_agents_project ON agents(project_id);
CREATE INDEX IF NOT EXISTS idx_nia_events_project ON nia_events(project_id);
`;

export function initDatabase(workspacePath: string): Database {
  fs.mkdirSync(workspacePath, { recursive: true });
  const dbPath = path.join(workspacePath, "orchestrator.db");
  log.info(`Opening database: ${dbPath}`);

  db = new Database(dbPath);
  db.exec("PRAGMA journal_mode = WAL");
  db.exec("PRAGMA foreign_keys = ON");
  db.exec(SCHEMA);

  log.info("Database schema initialized");
  return db;
}

export function getDb(): Database {
  if (!db) throw new Error("Database not initialized — call initDatabase() first");
  return db;
}

export function resetDatabase(workspacePath: string): Database {
  if (db) {
    db.close();
    db = null;
  }
  const dbPath = path.join(workspacePath, "orchestrator.db");
  try {
    fs.unlinkSync(dbPath);
    fs.unlinkSync(dbPath + "-wal");
    fs.unlinkSync(dbPath + "-shm");
  } catch {
    // Files may not exist
  }
  return initDatabase(workspacePath);
}
