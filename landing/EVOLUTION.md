 KernelSwarm is a general-purpose optimization system that uses a 64-agent LLM swarm combined with MAP-Elites evolutionary search to discover
  high-performance solutions to any problem that can be programmatically scored. Feed it a problem definition — a baseline solution, a way to         
  build/validate candidates, and a scoring function — and the swarm will iteratively evolve better solutions by leveraging LLM reasoning for
  intelligent mutations.                       

  The architecture has three layers:

  1. Orchestration — 32 generator agents propose candidate mutations and 32 judge agents triage them for quality, all coordinated by a central
  scheduler.
  2. Evolutionary Search — A MAP-Elites archive spread across 4 islands with ring-topology migration. Candidates are indexed by behavioral descriptors
   so the system preserves diverse high-performing solutions, not just a single optimum. Islands periodically exchange their best candidates to
  cross-pollinate strategies.
  3. Evaluation — Candidates are built, validated for correctness, and benchmarked via an eval pipeline that can run locally or remotely over HTTP —
  enabling a "local brain, remote muscle" topology where LLM inference and heavy evaluation happen on separate machines.

  The system is fully plugin-based. A problem plugin defines the interface: what a baseline looks like, how to build and validate candidates, how to
  benchmark and score them, and what behavioral descriptors characterize a solution. This makes the core engine domain-agnostic — GPU kernel
  optimization, algorithm tuning, configuration search, code generation, or any other domain where solutions can be evaluated programmatically.

  The LLM backbone (currently Nemotron via DeepInfra or NVIDIA NIM) drives intelligent exploration: generators see the current best solution,
  evaluation feedback (including errors and profiler metrics), and propose structured mutations. Judges quickly filter out broken or redundant
  proposals before they hit the expensive eval pipeline. This makes the search far more sample-efficient than blind evolutionary approaches.

  Everything is designed for reproducibility and fault tolerance — candidates follow a 14-state FSM, all artifacts are content-addressed with SHA-256,
   SQLite persistence tracks every agent call and budget event, and checkpointing enables deterministic resume from any snapshot. A live dashboard
  provides real-time observability into fitness curves, island coverage, and candidate leaderboards.

  The initial focus is GPU kernel discovery (with plugins for vector addition, reduction, 2D stencil, and Stanford's KernelBench suite of 250
  problems), but the plugin system means any optimization problem with a scorable solution space is fair game.