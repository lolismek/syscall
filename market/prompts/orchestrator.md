# Orchestrator (Main Server)

The orchestrator plans a project, breaks it into tasks with a dependency DAG, and coordinates worker agents via MCP. It validates submissions using an LLM and merges accepted work to main.

## Run Command

```bash
npm start "<project description>"
```

## Example

```bash
npm start "Build a RESTful todo API using Node.js and Express with TypeScript. It should support full CRUD: create a todo (POST /todos), list all todos (GET /todos), get a single todo (GET /todos/:id), update a todo (PUT /todos/:id), and delete a todo (DELETE /todos/:id). Each todo has an id, title, completed boolean, and createdAt timestamp. Use an in-memory array as the data store. Include input validation (title is required, must be a non-empty string). Return proper HTTP status codes (201 for creation, 404 for not found, 400 for validation errors). Add a GET /health endpoint. Structure the code into separate files: routes, handlers, types, and a main server entry point."
```

## Dashboard

```bash
npm run dashboard
```

Opens at http://localhost:3200. Shows real-time dependency graph, task board, agent status, and progress.

## Ports

- Orchestrator MCP server: `3100` (configurable via `PORT` env var)
- Dashboard: `3200` (configurable via `DASHBOARD_PORT` env var)

## Environment Variables

- `ANTHROPIC_API_KEY` — Required
- `MODEL` — Anthropic model (default: claude-sonnet-4-20250514)
- `PORT` — MCP server port (default: 3100)
- `WORKSPACE_PATH` — Git workspace path (default: ./workspace)
- `LOG_LEVEL` — debug | info | warn | error (default: info)
