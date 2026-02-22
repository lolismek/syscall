# CLAUDE.md

## Project

Syscall Market — multi-agent code orchestrator. An MCP server (Express + StreamableHTTPServerTransport) powered by the Claude Agent SDK that plans coding projects, distributes tasks to worker agents, validates submissions, and merges accepted code.

## Commands

```bash
bun start "Build a todo REST API"         # Start server + create project (dashboard at :3100)
bun start -- --fresh "Build a todo API"   # Force re-plan (wipe state)
bun start -- --model claude-opus-4-5-20250514 "Build X"
bun run worker                            # Test worker (no LLM)
bun run build                             # TypeScript compile
```

## Tech Stack

- TypeScript (ESM, `"type": "module"`)
- Node.js 20+
- Express 4 for HTTP
- `@modelcontextprotocol/sdk` for MCP server/transport
- `@anthropic-ai/claude-code` (Claude Agent SDK) for orchestrator intelligence
- Zod for MCP tool input validation

## Key Files

- `src/index.ts` — entry point, CLI arg parsing, state hydration, timeout sweep
- `src/mcp/server.ts` — 6 MCP tool definitions (join_project, get_my_task, report_status, check_updates, submit_result, get_project_context)
- `src/mcp/transport.ts` — Express app, session management, auth middleware, /api/status endpoint
- `src/orchestrator/actions.ts` — `planProject()` and `validateSubmission()` (calls Claude Agent SDK)
- `src/orchestrator/invoke.ts` — Claude Agent SDK `query()` wrapper
- `src/orchestrator/prompts/` — LLM prompt templates for planning and validation
- `src/state/task-board.ts` — task + agent state, persistence, timeout tracking, cycle detection
- `src/state/project-store.ts` — project state, persistence
- `src/git/repo.ts` — git operations (init, branch, diff, merge), serialized via promise-chain lock
- `src/utils/config.ts` — config from env vars (port, model, timeout, API key)
- `src/dashboard/` — unified dashboard (served at `/` on same port)
  - `html.ts` — main assembler, exports `getDashboardHtml()`
  - `styles.ts` — CSS design system (zinc + indigo theme, Inter/JetBrains Mono fonts)
  - `views.ts` — HTML template functions (hero, projects, for-agents, detail)
  - `client.ts` — client-side JS (polling, rendering, view switching)

## Architecture Patterns

- **State persistence**: `TaskBoard` and `ProjectStore` write to `.orchestrator-state.json` (project root) on every mutation. Atomic writes (tmp + rename) with coalesced serialization to avoid concurrent write races.
- **Git locking**: all mutating git ops (add, commit, merge, branch) serialized via a promise-chain lock in `GitRepo.withLock()`.
- **MCP sessions**: each agent connection gets its own `StreamableHTTPServerTransport` instance, tracked by session ID in a Map.
- **Task timeout**: `lastActivityAt` on tasks, refreshed by any agent MCP call. 60s interval sweep reassigns stale tasks.
- **Auth**: optional Bearer token middleware on `/mcp` routes. Dashboard/health routes are unprotected.
- **Cycle detection**: DFS-based detection on task dependency DAG after planning. Auto-breaks cycles by removing back-edges.

## Conventions

- All imports use `.js` extensions (ESM)
- Logger: `createLogger("ComponentName")` → `[timestamp] [LEVEL] [Component] message`
- Config: all env vars read in `src/utils/config.ts`, imported as `config`
- Task IDs: `task-001`, `task-002`, etc. Agent IDs: `agent-{name}-{timestamp}`
- Branches: `agent/{agent-id}/{task-id}`
- The orchestrator never writes application code — only scaffold, types, and config

## State File

`.orchestrator-state.json` in project root (gitignored). Contains:
```json
{
  "tasks": [["task-001", {...}], ...],
  "agents": [["agent-alice-xxx", {...}], ...],
  "nextTaskNum": 6,
  "project": { "id": "...", "name": "...", ... }
}
```
Dates serialized as ISO strings, rehydrated on load.

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | API key for Claude Agent SDK |
| `MODEL` | `claude-4-sonnet-20250514` | Override with `--model` flag |
| `PORT` | `3100` | MCP server port |
| `WORKSPACE_PATH` | `./workspace` | Git workspace (wiped on fresh start) |
| `LOG_LEVEL` | `info` | debug/info/warn/error |
| `AGENT_API_KEY` | (unset) | Bearer token for /mcp auth |
| `TASK_TIMEOUT_MS` | `900000` | 15 min agent inactivity timeout |
