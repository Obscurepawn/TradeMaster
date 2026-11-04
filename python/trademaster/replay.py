"""Replay a saved event or snapshot request with the installed Rust engine."""

import argparse
import sys
from pathlib import Path

from trademaster import _native


def replay(request_path: str | Path) -> bytes:
    """Run the saved request and return its original result format."""
    path = Path(request_path)
    return _native.replay_request(path.read_bytes(), str(path.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="Path to a saved request.json")
    args = parser.parse_args()
    try:
        result = replay(args.request)
    except (OSError, ValueError) as error:
        parser.exit(1, f"tm-replay: {error}\n")
    sys.stdout.buffer.write(result)


if __name__ == "__main__":
    main()
