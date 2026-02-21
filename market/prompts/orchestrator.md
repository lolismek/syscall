# Orchestrator (Main Server)

The orchestrator plans projects, breaks them into tasks with a dependency DAG, and coordinates worker agents via MCP. It validates submissions using an LLM and merges accepted work to main. Supports multiple concurrent projects.

## Run Commands

```bash
# Server-only mode (create projects via dashboard or API)
npm start

# Create a project on startup
npm start -- "Build a RESTful todo API"

# Force re-plan
npm start -- --fresh "Build a todo API"

# Override model
npm start -- --model claude-opus-4-5-20250514 "Build X"
```

## Creating Projects

**Dashboard** (recommended): Open http://localhost:3200, type your idea, click Create.

**API**:
```bash
curl -X POST localhost:3100/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"idea":"Build a chat app"}'
```

**CLI**: Pass the idea as an argument to `npm start`.

## Dashboard

```bash
npm run dashboard
```

Opens at http://localhost:3200. Shows project list with create form, per-project dependency graph, task board, agent status, and progress.

## Ports

- Orchestrator MCP server: `3100` (configurable via `PORT` env var)
- Dashboard: `3200` (configurable via `DASHBOARD_PORT` env var)

## Environment Variables

- `ANTHROPIC_API_KEY` — Required
- `MODEL` — Anthropic model (default: claude-4-sonnet-20250514)
- `PORT` — MCP server port (default: 3100)
- `WORKSPACE_PATH` — Git workspace path (default: ./workspace)
- `LOG_LEVEL` — debug | info | warn | error (default: info)
- `GITHUB_ORG` — GitHub organization for auto-creating repos
- `GITHUB_TOKEN` — GitHub personal access token (required if GITHUB_ORG is set)
- `AGENT_API_KEY` — Shared Bearer token for /mcp auth (unset = no auth)
- `TASK_TIMEOUT_MS` — Agent inactivity timeout in ms (default: 900000 = 15min)
