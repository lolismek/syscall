# Syscall — Multi-Agent Code Orchestrator MVP

## One-liner
A central MCP server powered by the Claude Agent SDK that takes a coding project idea, breaks it into tasks, and distributes ALL the actual work to any agents that connect — regardless of what powers them.

## Architecture

```
Agent A (MCP client) →
Agent B (MCP client) → ORCHESTRATOR (Claude Agent SDK + MCP server) → GitHub Repo
Agent C (MCP client) →
```

- The orchestrator is the ONLY MCP server. Agents connect to it as MCP clients.
- Agents can be anything: Claude Code, Codex, OpenClaw/Moltbot, a local LLM, whatever. If it can be an MCP client, it can participate.
- The orchestrator is powered by the Claude Agent SDK — the same full agent harness that runs Claude Code.

## The Claude Agent SDK

The orchestrator is NOT built with raw Claude API calls. It uses the Claude Agent SDK (formerly Claude Code SDK), which is Anthropic's full agent harness — the same one that powers Claude Code. This gives the orchestrator:

- **Agentic loop**: gather context → take action → verify → repeat. The SDK handles the loop, tool invocations, and session management automatically.
- **Built-in tools**: bash, file read/write, grep, glob, git — the orchestrator can run tests, commit code, inspect the repo, all natively.
- **Context management**: automatic compaction and summarization so the orchestrator can manage a long-running project without exhausting its context window.
- **Subagents**: the SDK supports spawning subagents for parallel work if needed.

In code, the orchestrator is a Claude Agent SDK agent with a system prompt that defines its role as a project manager, plus an MCP server that exposes tools for external worker agents to connect.

```python
from claude_agent_sdk import query, ClaudeAgentOptions

# The orchestrator is an agent
async for message in query(
    prompt="A user wants to build X. Break it into tasks and manage the project.",
    options=ClaudeAgentOptions(
        system_prompt="You are a project orchestrator. You NEVER write code yourself. You break projects into tasks, assign them to worker agents, validate their submissions, and commit accepted code to the repo. You are a tech lead, not a developer.",
        allowed_tools=["Bash", "Read", "Write", "Glob", "Grep"],
        # + MCP server tools for agent communication
    ),
)
```

## Critical Design Principle: The Orchestrator Does NOT Do The Work

The orchestrator is a tech lead / project manager. It:
- ✅ Plans and decomposes projects into tasks
- ✅ Writes interface contracts and shared type definitions
- ✅ Assigns tasks to worker agents
- ✅ Validates submitted code (runs tests, checks contracts)
- ✅ Commits accepted code to the GitHub repo
- ✅ Re-plans when things change or fail
- ❌ NEVER writes application code itself
- ❌ NEVER implements features
- ❌ NEVER fixes bugs in agent submissions (sends feedback instead)

If no agents are connected, the orchestrator waits. It does not fall back to doing the work itself. The whole point is delegation.

The only code the orchestrator may write directly is:
- Scaffold files (project structure, package.json, tsconfig, etc.)
- Shared type definitions and interface contracts
- Test harnesses that validate agent submissions
- Configuration files

Everything else is delegated.

## Core Flow

1. **User submits a project idea** to the orchestrator (via CLI or HTTP endpoint)
2. **Orchestrator plans**: uses its agentic loop to break the idea into scoped tasks with descriptions, specs, interface contracts, and dependency ordering. May scaffold the initial project structure and shared types.
3. **Agents connect** to the MCP server and call `join_project()`
4. **Agents pull tasks** by calling `get_my_task()` — the orchestrator assigns the next available unblocked task with rich Claude-generated instructions
5. **Agents work autonomously** using whatever underlying model/tool they have
6. **Agents submit results** by calling `submit_result(task_id, files[])`
7. **Orchestrator validates** using its built-in tools (runs tests via bash, checks interface contracts, reads the code). Accepts or rejects with detailed feedback.
8. **If accepted**: orchestrator commits to GitHub repo
9. **If rejected**: agent gets feedback and can resubmit
10. **Loop**: agents call `get_my_task()` again for the next task

## MCP Server Tools (what agents call)

```
join_project(project_id, agent_name, agent_capabilities)
  → { welcome message, project summary, repo url, rules of engagement }

get_my_task()
  → { task_id, instructions (Claude-generated), specs, interface_contracts, repo_url, branch_name, rules }
  → rules include: "call check_updates before submitting", "call report_status when you start"

report_status(task_id, status, progress_description)
  → ack

check_updates(task_id)
  → { updates[] } — spec changes, dependency completions, interface changes

submit_result(task_id)
  → lightweight signal: "my branch is ready for review"
  → orchestrator pulls the branch, validates, returns { accepted/rejected, feedback if rejected }

get_project_context(file_paths[])
  → { file contents from main branch } (convenience tool, agents can also just read from their local clone)
```

## Communication Model

- MCP is pull-based: agents initiate all interactions, orchestrator cannot push
- The orchestrator encodes its intelligence into tool responses — rich instructions, update payloads, rejection feedback
- Agents are expected to: call report_status when starting, call check_updates before submitting, loop back to get_my_task after completing work
- This is naturally compatible with async agent patterns (OpenClaw heartbeats, Claude Code sessions, etc.)

## Code Sharing

- A GitHub repo is the canonical workspace.
- Each agent gets its own branch (e.g., `agent/auth-middleware-001`) assigned by the orchestrator as part of the task.
- Agents clone the repo, work on their branch, and push commits directly. Git is just a CLI tool — any agent capable of writing code can use it.
- When done, the agent calls `submit_result(task_id)` over MCP — this is a lightweight signal ("review my branch"), NOT a file transfer.
- The orchestrator pulls the branch, reviews the diff, runs tests, and validates against interface contracts.
- If accepted: the orchestrator merges the branch into main.
- If rejected: the orchestrator sends feedback via MCP, the agent pushes fixes to the same branch.
- The orchestrator is the only one who merges to main. Agents own their branches, orchestrator owns main.
- Shared files (types, interfaces, configs) live on main and are maintained by the orchestrator.

## Orchestrator Internals

- **Claude Agent SDK**: powers the orchestrator as a full agent with agentic loop, built-in tools (bash, file ops, git), context management with compaction, and subagent support. The SDK handles the reasoning loop — the orchestrator doesn't need manual prompt chaining.
- **MCP Server**: runs alongside the agent, exposing the 6 tools above for worker agents to call. This is the communication layer.
- **GitHub repo**: canonical workspace. Agents push to their own branches. The orchestrator reviews diffs, runs tests, and merges accepted branches into main. Only the orchestrator merges to main.
- **Task state**: simple in-memory or JSON-file task board tracking: task status (pending/assigned/in_progress/completed/failed), which agent owns it, dependencies/blocking.

## What This Proves

- The Claude Agent SDK can power an orchestrator that manages external agents
- A single MCP server can coordinate heterogeneous agents
- Pull-based task distribution works for autonomous agent coordination
- Delegation-first design: the orchestrator plans and validates but never builds
- Agent-agnostic design: any MCP client can participate

## Out of Scope for MVP

- Multi-project support (just one project at a time)
- Dashboard UI
- Agent discovery / matchmaking
- Token economics / incentive systems
- Persistent storage (in-memory is fine)
- Authentication / trust between agents

## Tech Stack

- Python or TypeScript (orchestrator server)
- Claude Agent SDK (`claude-agent-sdk`) for the orchestrator's agentic intelligence
- MCP SDK (`@modelcontextprotocol/sdk`) for the MCP server
- GitHub repo as canonical workspace (orchestrator is sole committer)
- git (via bash in the Agent SDK) for repo operations
- JSON files for task state

## To Build

1. Orchestrator agent using Claude Agent SDK with a system prompt enforcing delegation-only behavior
2. MCP server with the 6 tools, running alongside the orchestrator
3. Planning logic: user prompt → task DAG with specs and interface contracts
4. Validation logic: submitted code → run tests → accept/reject with feedback
5. GitHub integration: commit accepted code to repo
6. A test worker agent (simple MCP client that connects and does work) to prove the full loop