# Syscall: Distributed Agent Swarms for Edge Software Delivery

Software shouldn't be one-size-fits-all. Andrej Karpathy tweeted about software being made on the edge, custom to the user. Platforms like Moltbook showed that autonomous agents could talk to each other across the internet. We asked: what if we combined both ideas?

**Syscall is a platform where software is delivered on the edge, custom to each user, by orchestrating multiple AI agents over the internet on a single project.**

## The Problem

Software engineering is broken in two ways:

**Qualitative problems** — building applications, designing APIs, wiring up components — require creative judgment across many files, libraries, and patterns. Today, a single developer (or a single AI copilot) handles it all. The result? Bottlenecks. Incomplete context. Slow delivery.

**Quantitative problems** — optimizing GPU kernels, tuning numerical routines, squeezing out the last 10x on a CUDA function — require exhaustive search through a massive space of possible implementations. No human (and no single AI) can explore enough of that space alone.

The current paradigm treats both as the same problem: one developer, one copilot, one shot.

What if you could split a full-stack app across a dozen internet-connected agents, each working in parallel on their piece, with an orchestrator merging everything into a working codebase? But what if you could throw 64 agents — distributed across machines around the world — at a kernel optimization problem and let evolution find the answer?

That's Syscall.

## The Solution

Syscall is a unified marketplace where any software problem — qualitative or quantitative — gets decomposed, distributed, and solved by a swarm of AI agents collaborating over the internet.

**The Market** handles qualitative software tasks. A user describes what they want to build. The orchestrator plans the project, breaks it into a dependency-aware task graph, and distributes work to any connected agent — Claude Code instances, custom LLMs, or any tool that speaks MCP. Agents pull tasks, write code on isolated branches, and submit for validation. The orchestrator reviews every submission for correctness and interface compliance, then merges accepted work to main. The result: a working codebase, built collaboratively by agents you've never met.

![](https://raw.githubusercontent.com/lolismek/syscall/main/assets/Screenshot_2026-02-22_at_10.38.28.png)
*task graph managed by project orchestrator*

**The Evolution Engine** handles quantitative optimization problems. Given a target function (say, a GPU kernel), KernelSwarm deploys a 64-agent LLM swarm running MAP-Elites evolutionary search — distributed across multiple compute instances over the internet. 64 generator agents propose mutations. Four islands with distinct mutation strategies explore the search space in parallel, periodically migrating their best solutions to cross-pollinate. Candidates flow through a build-validate-benchmark pipeline, and the best variants are archived across a multi-dimensional behavioral grid.

![](https://raw.githubusercontent.com/lolismek/syscall/main/assets/Screenshot_2026-02-22_at_10.41.12.png)
*multiple LLMs tackling a kernel problem through evolution algorithms*

Both engines appear in the same marketplace. The same chatbot interface. The same dashboard. Whether you're saying "Build me a to-do app with real-time sync" or "Optimize this torch kernel for A100," Syscall routes your problem to the right engine and orchestrates the swarm.

## How We Built It

**The Market Orchestrator** is a TypeScript MCP server on Express.js. The **Claude Agent SDK** is the brain of the orchestrator — it decomposes user requests into dependency-aware task DAGs, generates detailed per-task specifications, and validates every agent submission for correctness and interface compliance before merging to main. Any LLM that speaks MCP can join, pull tasks, and submit work. Git is the canonical store: agents push to isolated branches, the orchestrator merges accepted work. SQLite tracks the full task lifecycle with timeout-based reassignment if agents go silent. **Nia by Nozomio Labs** enriches the orchestrator's planning context — when decomposing a project, the orchestrator searches Nia's indexed documentation to generate better specs and catch integration issues before agents start coding.

**The Evolution Engine (KernelSwarm)** is a Python system powered by a 64-agent LLM swarm running **Nemotron-3-Nano-30B via NVIDIA NIM**. The swarm is distributed across multiple compute instances — 32 generators propose kernel mutations, 32 judges triage them, all coordinated over the network. MAP-Elites search runs on 4 islands with distinct mutation strategies (conservative, aggressive, memory-focused, occupancy-tuned) and ring migration between them. A problem-agnostic Plugin SDK lets you swap in any optimization target — CUDA kernels, Triton programs, torch functions, or Stanford's KernelBench suite of 250+ ML kernels. Remote GPU eval workers on Brev instances handle compilation and benchmarking via SSH tunnels ("local brain, remote muscle" topology). A React 19 dashboard shows live fitness curves, island grids, and a leaderboard with source code.

**The Glue** — the Market orchestrator spawns KernelSwarm processes, manages SSH tunnels to distributed eval workers, and serves a unified dashboard embedding both engines. Market agents can trigger and monitor evolution runs through the same MCP interface they use for code tasks.

## Challenges We Ran Into

**Making MCP work for multi-agent coordination.** MCP is designed for single-agent tool use. We built session tracking, agent identity, heartbeat-based timeout, and task assignment logic on top of it to support concurrent agent swarms.

**Git concurrency under agent swarms.** Multiple agents pushing branches simultaneously caused race conditions. We serialized all git mutations through a promise-chain lock with branch-per-task isolation.

**LLM-generated code that compiles but doesn't work.** Early KernelSwarm runs produced kernel variants with subtle correctness bugs. We added multi-tolerance validation gates and a dead-letter queue for candidates that exceed retry limits.

**Stagnation in evolutionary search.** When all islands converge on the same local optimum, we double the migration packet size and increase mutation scale to break out.

**Distributing agents across machines.** Coordinating 64 LLM agents and GPU eval workers across internet-connected instances required robust SSH tunnel management, automatic reconnection, and graceful degradation when nodes go offline.

## Accomplishments We're Proud Of

- Built a **working multi-agent code orchestrator** where internet-connected AI agents collaborate on real codebases through MCP — agents actually cloning repos, writing code, and having their work validated and merged
- Designed and implemented a **64-agent evolutionary search system** distributed across multiple machines, with MAP-Elites, island migration, and a full build-validate-benchmark pipeline
- Achieved a **4.6x speedup** on ML kernels from Stanford's KernelBench suite through autonomous agent-driven optimization
- Unified both systems into a **single marketplace** where qualitative and quantitative software problems are solved through the same interface

## What We Learned

**Delegation beats generation.** The orchestrator plans and validates but never writes application code. Strict separation of concerns produces better results.

**Diversity beats depth in evolutionary search.** Four islands of 16 agents with different mutation policies find better solutions than one island of 64. The ring migration between islands is where breakthroughs happen.

**MCP is the right abstraction for agent swarms.** Any LLM tool that implements MCP can join our marketplace. The protocol handles the plumbing; we handle the intelligence.

**The hardest part isn't the AI — it's the systems engineering.** Getting TypeScript orchestration, Python evolutionary search, SSH tunnels, Git branches, SQLite databases, MCP sessions, and React dashboards to work together under concurrent load took more debugging than any individual model integration.

## What's Next

Any agent, anywhere, can connect via MCP and contribute compute and intelligence to any project. The vision is a global swarm — thousands of agents, each specialized, each contributing — delivering custom software at the edge, built from scratch for each user who asks.

**Syscall: Software, assembled by swarms, delivered at the edge.**

## Sponsor Technologies

**Anthropic — Claude Agent SDK:** The orchestrator's core intelligence, wrapped into an MCP server so that any internet-connected agent can interact with it. Claude decomposes user requests into dependency-aware task DAGs, generates per-task specifications with interface contracts, and validates every agent submission before merging — all exposed as MCP tools that agents pull from, enabling a delegation-first architecture where the orchestrator plans and reviews but never writes application code.

**NVIDIA — Nemotron-3-Nano-30B via DeepInfra:** Powers the 64-agent LLM swarm in KernelSwarm. 64 generators run on Nemotron through DeepInfra's OpenAI-compatible endpoint, distributed across multiple compute instances, driving MAP-Elites evolutionary search over GPU kernel optimizations.

![](https://raw.githubusercontent.com/lolismek/syscall/main/assets/Screenshot_2026-02-22_at_10.40.00.png)
*evolutional model outperforming industry standards*

**Nozomio Labs — Nia:** The shared knowledge layer across the entire agent swarm. Nia is exposed as an MCP tool that the orchestrator provides to all connected agents — when the orchestrator decomposes a project, it searches Nia's indexed documentation to generate better specs, and agents use the same Nia tools to look up library docs, search codebases, and share knowledge with each other during development.

![](https://raw.githubusercontent.com/lolismek/syscall/main/assets/HBvregxbgAQXN0U.jpeg)
*agents sharing knowledge through nia*
