"""Run the research API and, when built, the Shadcn dashboard."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the margin-of-safety dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    uvicorn.run("src.api.server:app", host=args.host, port=args.port, factory=False)


if __name__ == "__main__":
    main()
