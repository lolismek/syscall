#!/usr/bin/env python3
"""Launch the syscall server."""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="syscall evolutionary server")
    parser.add_argument("--problem", default="two_sum", help="Problem name to load")
    parser.add_argument("--generations", type=int, default=10, help="Max generations")
    parser.add_argument("--top-k", type=int, default=3, help="Top K solutions to keep")
    parser.add_argument("--timeout", type=int, default=60, help="Generation timeout (seconds)")
    parser.add_argument("--min-agents", type=int, default=1, help="Wait for N agents before starting")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    args = parser.parse_args()

    import syscall.server as srv
    srv.CONFIG.update(
        problem_name=args.problem,
        max_generations=args.generations,
        top_k=args.top_k,
        generation_timeout=args.timeout,
        min_agents=args.min_agents,
    )

    uvicorn.run("syscall.server:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    main()
