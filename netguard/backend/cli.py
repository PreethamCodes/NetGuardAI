"""``python -m netguard serve`` - start the FastAPI backend."""

from __future__ import annotations

import argparse
from typing import Any


def register(sub: Any) -> None:
    sp = sub.add_parser("serve", help="Phase 6: start the backend API (needs trained models)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from netguard.backend.app import create_app

    uvicorn.run(create_app(config_dir=args.config_dir), host=args.host, port=args.port, log_level="info")
    return 0
