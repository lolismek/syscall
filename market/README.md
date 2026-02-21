# Syscall — Multi-Agent Code Orchestrator

A central MCP server powered by the Claude Agent SDK that takes a coding project idea, breaks it into tasks, and distributes all the work to any agents that connect — regardless of what powers them.

```
Agent A (Claude Code) ─┐
Agent B (Codex)       ─┤─► ORCHESTRATOR (MCP server + Claude Agent SDK) ─► Git Repo
Agent C (any LLM)     ─┘
```

## Quick Start

```bash
# Install
npm install

# Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY

# Run orchestrator
npm start -- "Build a todo REST API"

# Run dashboard (separate terminal)
npm run dashboard
```

## Running Worker Agents

Workers are Claude Code sessions (or any MCP client) that connect to the orchestrator.

### 1. Add the MCP server to Claude Code

```bash
claude mcp add orchestrator --transport http http://localhost:3100/mcp
```

With auth enabled:

```bash
claude mcp add orchestrator --transport http http://localhost:3100/mcp \
  --header "Authorization: Bearer <your-AGENT_API_KEY>"
```

To update the config (e.g. add auth), remove and re-add:

```bash
claude mcp remove orchestrator && \
claude mcp add orchestrator --transport http http://localhost:3100/mcp \
  --header "Authorization: Bearer <your-AGENT_API_KEY>"
```

### 2. Start a worker

```bash
claude -p prompts/worker-alice.md
```

Or for multiple workers, use separate terminals with `worker-alice.md`, `worker-bob.md`, etc.

### 3. Test worker (no LLM needed)

```bash
npm run worker
```

Runs `test-worker/index.ts` — a minimal MCP client that exercises the full lifecycle with placeholder code.

## CLI Options

```bash
npm start -- [options] "<project idea>"
```

| Flag | Description |
|---|---|
| `--model <model>` / `-m` | Anthropic model for planning/validation |
| `--fresh` | Delete saved state and re-plan from scratch |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key |
| `MODEL` | `claude-4-sonnet-20250514` | Model for orchestrator LLM calls |
| `PORT` | `3100` | MCP server port |
| `WORKSPACE_PATH` | `./workspace` | Git workspace directory |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `AGENT_API_KEY` | (unset) | Shared Bearer token for `/mcp` auth. Unset = no auth. |
| `TASK_TIMEOUT_MS` | `900000` (15 min) | Agent inactivity timeout before task reassignment |

## Architecture

### Orchestrator (`src/index.ts`, `src/orchestrator/`)

Takes a project idea, calls the Claude Agent SDK to plan it (decompose into tasks, write scaffold), then starts the MCP server. On task submission, calls the SDK again to validate the diff.

### MCP Server (`src/mcp/`)

Express HTTP server with `StreamableHTTPServerTransport`. Exposes 6 tools:

- `join_project` — register as a worker, get project summary + repo URL
- `get_my_task` — get next available task (respects dependency ordering)
- `report_status` — report progress (keeps timeout alive)
- `check_updates` — poll for validation results
- `submit_result` — submit branch for review
- `get_project_context` — read files from main branch

### State (`src/state/`)

`TaskBoard` (task + agent tracking, event emitter) and `ProjectStore` (active project). Both persist to `.orchestrator-state.json` on every mutation. Atomic writes (tmp + rename) with coalesced serialization to avoid races.

### Git (`src/git/repo.ts`)

Manages a local git repo at `./workspace`. Creates branches for agents, diffs submissions against main, merges accepted work. All mutating ops serialized via a promise-chain lock.

### Dashboard (`src/dashboard.ts`)

Standalone server on port 3200. Polls `/api/status` and renders a live SVG dependency graph + task board.

## Reliability Features

- **Persistence**: State survives restarts. Hydrates from `.orchestrator-state.json` on startup. Use `--fresh` to force re-plan.
- **Task timeout**: Agents that go silent (no MCP calls for 15 min) get their tasks reassigned. Agents that are slow but still calling `report_status` are not affected.
- **Auth**: Optional `AGENT_API_KEY` protects `/mcp` routes. Dashboard and health endpoints remain open.
- **Branch verification**: `submit_result` checks the branch exists and has commits before calling the LLM validator. Prevents wasted validation calls.
- **Cycle detection**: After planning, the task DAG is checked for circular dependencies. Cycles are auto-broken by removing back-edges.

## Project Structure

```
src/
  index.ts                  # Entry point, CLI, startup, timeout sweep
  dashboard.ts              # Standalone dashboard server
  git/repo.ts               # Git operations (branch, diff, merge)
  mcp/
    server.ts               # MCP tool definitions (6 tools)
    transport.ts            # Express HTTP + auth middleware
  orchestrator/
    actions.ts              # planProject, validateSubmission
    invoke.ts               # Claude Agent SDK wrapper
    prompts/                # LLM prompt templates
  state/
    task-board.ts           # Task + agent state, persistence, cycle detection
    project-store.ts        # Project state, persistence
  types/                    # TypeScript interfaces
  utils/
    config.ts               # Config from env vars
    logger.ts               # Structured logger
prompts/                    # Worker agent prompt templates
test-worker/                # Smoke-test MCP client
workspace/                  # Live git repo (gitignored)
```
