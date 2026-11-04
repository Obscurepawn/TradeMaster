"""Measure native event replay with a fixed universe and increasing history."""

import argparse
import json
import statistics
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trademaster import _native


def request_bytes(example: dict[str, Any], sessions: int, symbols: int) -> bytes:
    ids = [f"{600000 + i}.SH" for i in range(symbols)]
    instrument = example["instruments"][0]
    bar = example["events"][0]["bars"][0]
    bars = [dict(bar, instrument_id=symbol) for symbol in ids]
    start = date(2020, 1, 1)
    # Dates act as synthetic sessions. The experiment measures history size.
    opens = [f"{start + timedelta(days=i)}T01:30:00Z" for i in range(sessions)]
    events = [
        {"kind": "bar_close", "event_time": f"{day[:10]}T07:00:00Z", "bars": bars} for day in opens
    ]
    request = dict(
        example,
        instruments=[dict(instrument, instrument_id=symbol) for symbol in ids],
        initial_time="2019-12-31T00:00:00Z",
        session_opens=opens,
        events=events,
        signals=[],
    )
    return json.dumps(request, separators=(",", ":")).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    example = json.loads((repo / "docs/examples/round-trip.json").read_bytes())
    print("sessions,symbols,median_seconds")
    for n in (64, 128, 256, 512):
        payload = request_bytes(example, n, 16)
        _native.run_event(payload)
        samples = []
        for _ in range(5):
            before = time.perf_counter()
            _native.run_event(payload)
            samples.append(time.perf_counter() - before)
        print(f"{n},16,{statistics.median(samples):.4f}", flush=True)


if __name__ == "__main__":
    main()
