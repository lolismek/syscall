"""FastAPI server with WebSocket-based evolutionary loop."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from syscall.models import (
    EvolveMessage,
    GenerateMessage,
    GenerationState,
    Problem,
    ScoredSubmission,
    StateUpdateMessage,
    StopMessage,
    SubmissionMessage,
)
from syscall.problems import load_problem
from syscall.sandbox import score_submission

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("syscall.server")

# Mutable config populated by run_server.py before uvicorn starts
CONFIG: dict[str, Any] = {
    "problem_name": "two_sum",
    "max_generations": 10,
    "top_k": 3,
    "generation_timeout": 60,
}

app = FastAPI(title="syscall")


# ── Connection registries ────────────────────────────────────────────────────

agent_connections: dict[str, WebSocket] = {}
agent_models: dict[str, str] = {}
dashboard_connections: list[WebSocket] = []


# ── EvolutionManager ─────────────────────────────────────────────────────────

class EvolutionManager:
    def __init__(self, problem: Problem, top_k: int, max_generations: int, generation_timeout: float):
        self.problem = problem
        self.top_k_count = top_k
        self.max_generations = max_generations
        self.generation_timeout = generation_timeout

        self.generation = 0
        self.phase: str = "waiting"
        self.submissions: list[ScoredSubmission] = []
        self.leaderboard: list[ScoredSubmission] = []
        self.history: list[dict[str, Any]] = []
        self._submission_event = asyncio.Event()

    def _state(self) -> GenerationState:
        return GenerationState(
            generation=self.generation,
            top_k=self.leaderboard[:self.top_k_count],
            all_submissions_count=len(self.submissions),
            connected_agents=len(agent_connections),
            phase=self.phase,
            best_time_ms=self.leaderboard[0].execution_time_ms if self.leaderboard else None,
        )

    async def broadcast_state(self):
        msg = StateUpdateMessage(state=self._state(), history=self.history)
        data = msg.model_dump_json()
        dead = []
        for ws in dashboard_connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            dashboard_connections.remove(ws)

    async def broadcast_to_agents(self, message):
        data = message.model_dump_json()
        dead = []
        for aid, ws in agent_connections.items():
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(aid)
        for aid in dead:
            agent_connections.pop(aid, None)

    async def run(self):
        # Wait for at least one agent
        self.phase = "waiting"
        await self.broadcast_state()
        log.info("Waiting for agents to connect...")
        while not agent_connections:
            await asyncio.sleep(0.5)

        # Give a moment for additional agents
        await asyncio.sleep(2)
        log.info(f"Starting with {len(agent_connections)} agent(s)")

        # Generation 0: generate from scratch
        self.phase = "generating"
        self.generation = 0
        self.submissions = []
        self._submission_event.clear()

        # Strip hidden test cases from what agents see
        visible_problem = self.problem.model_copy(update={"hidden_test_cases": []})

        await self.broadcast_to_agents(GenerateMessage(problem=visible_problem, generation=0))
        await self.broadcast_state()
        await self._wait_for_generation()
        self._finalize_generation()
        await self.broadcast_state()

        # Generations 1..N: evolve
        for gen in range(1, self.max_generations):
            self.generation = gen
            self.phase = "evolving"
            self.submissions = []
            self._submission_event.clear()

            top_solutions = self.leaderboard[:self.top_k_count]
            await self.broadcast_to_agents(
                EvolveMessage(problem=visible_problem, generation=gen, top_solutions=top_solutions)
            )
            await self.broadcast_state()
            await self._wait_for_generation()
            self._finalize_generation()
            await self.broadcast_state()

            log.info(
                f"Gen {gen} done — {len(self.submissions)} submissions, "
                f"best: {self.leaderboard[0].execution_time_ms:.1f}ms" if self.leaderboard else "no passing submissions"
            )

        # Stop
        self.phase = "stopped"
        best = self.leaderboard[0] if self.leaderboard else None
        await self.broadcast_to_agents(StopMessage(best_solution=best))
        await self.broadcast_state()
        log.info("Evolution complete!")
        if best:
            log.info(f"Best solution: {best.execution_time_ms:.1f}ms by {best.agent_id}")

    async def _wait_for_generation(self):
        target = 2 * len(agent_connections)
        try:
            deadline = self.generation_timeout
            while deadline > 0 and len(self.submissions) < target:
                self._submission_event.clear()
                try:
                    await asyncio.wait_for(self._submission_event.wait(), timeout=min(deadline, 5.0))
                except asyncio.TimeoutError:
                    pass
                deadline -= 5.0
        except Exception:
            pass
        log.info(f"Gen {self.generation}: collected {len(self.submissions)} submission(s)")

    def _finalize_generation(self):
        passing = [s for s in self.submissions if s.passed_all]
        combined = self.leaderboard + passing
        # Deduplicate: keep best per agent, then sort globally
        best_per_agent: dict[str, ScoredSubmission] = {}
        for s in combined:
            if s.agent_id not in best_per_agent or s.execution_time_ms < best_per_agent[s.agent_id].execution_time_ms:
                best_per_agent[s.agent_id] = s
        self.leaderboard = sorted(best_per_agent.values(), key=lambda s: s.execution_time_ms)

        best_ms = self.leaderboard[0].execution_time_ms if self.leaderboard else None
        self.history.append({"generation": self.generation, "best_time_ms": best_ms})

    async def handle_submission(self, msg: SubmissionMessage):
        log.info(f"Scoring submission from {msg.agent_id} (gen {msg.generation})")
        scored = await score_submission(msg, self.problem)
        log.info(
            f"  → {'PASS' if scored.passed_all else 'FAIL'} "
            f"({scored.execution_time_ms:.1f}ms)"
        )
        if msg.generation == self.generation:
            self.submissions.append(scored)
            self._submission_event.set()
            await self.broadcast_state()


manager: EvolutionManager | None = None


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global manager
    problem = load_problem(CONFIG["problem_name"])
    manager = EvolutionManager(
        problem=problem,
        top_k=CONFIG["top_k"],
        max_generations=CONFIG["max_generations"],
        generation_timeout=CONFIG["generation_timeout"],
    )
    asyncio.create_task(manager.run())
    log.info(f"Server started — problem={CONFIG['problem_name']}, generations={CONFIG['max_generations']}")


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "dashboard.html"
    return HTMLResponse(html_path.read_text())


@app.websocket("/ws/agent")
async def agent_ws(ws: WebSocket):
    await ws.accept()
    agent_id = None
    try:
        # First message must be register
        raw = await ws.receive_text()
        data = json.loads(raw)
        if data.get("type") != "register":
            await ws.close(code=1008, reason="First message must be register")
            return

        agent_id = data["agent_id"]
        model_name = data.get("model_name", "unknown")
        agent_connections[agent_id] = ws
        agent_models[agent_id] = model_name
        log.info(f"Agent registered: {agent_id} (model: {model_name})")

        if manager:
            await manager.broadcast_state()

        # Listen for submissions
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") == "submission" and manager:
                msg = SubmissionMessage(**data)
                asyncio.create_task(manager.handle_submission(msg))

    except WebSocketDisconnect:
        log.info(f"Agent disconnected: {agent_id}")
    except Exception as e:
        log.error(f"Agent error ({agent_id}): {e}")
    finally:
        if agent_id:
            agent_connections.pop(agent_id, None)
            agent_models.pop(agent_id, None)
            if manager:
                await manager.broadcast_state()


@app.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket):
    await ws.accept()
    dashboard_connections.append(ws)
    log.info("Dashboard connected")
    try:
        if manager:
            await manager.broadcast_state()
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        if ws in dashboard_connections:
            dashboard_connections.remove(ws)
        log.info("Dashboard disconnected")
