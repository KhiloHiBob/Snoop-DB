"""``python -m snoop_db`` / ``snoop-db``: start the local server (127.0.0.1 only)."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="snoop-db", description="Read-only AI database agent")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", help="path to a config file (default: config/local.yaml, else the example)")
    args = parser.parse_args()

    import uvicorn

    from .app import create_app
    from .config import load_config

    config = load_config(args.config)
    print(f"Snoop-DB using {config.source}")
    print(f"Open http://127.0.0.1:{args.port}  (environments: {', '.join(config.environments)})")
    # Always loopback: this app can read production data and has no authentication.
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
