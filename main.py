"""
main.py
Created by Amir (demiurg39) Chiryagov

CLI and API server entry point for the Beeline tariff recommendation engine.
"""

import argparse
import json
import sys
from pathlib import Path

from src import recommend
from src.exceptions import TariffCalculatorError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Beeline tariff recommendation CLI & API launcher.",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run FastAPI backend server")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")

    parser.add_argument(
        "usage_file",
        nargs="?",
        type=Path,
        help="Path to usage_<id>.csv. If omitted, evaluates all usage_*.csv in --data-dir.",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--tariff", type=str, default=None)
    parser.add_argument("--months", nargs=3, default=None, metavar=("M1", "M2", "M3"))
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("src.api:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    try:
        if args.usage_file is not None:
            result = recommend(
                args.usage_file,
                data_dir=args.data_dir,
                current_tariff_id=args.tariff,
                months=args.months,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        csv_files = sorted(args.data_dir.glob("usage_*.csv"))
        if not csv_files:
            print(f"No usage_*.csv files found in {args.data_dir}", file=sys.stderr)
            return 1

        results = [
            recommend(csv_path, data_dir=args.data_dir) for csv_path in csv_files
        ]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    except TariffCalculatorError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
