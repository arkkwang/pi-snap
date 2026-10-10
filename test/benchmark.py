"""Repeatable local benchmark: python3 test/benchmark.py (no private sessions)."""
import contextlib
import io
import json
from pathlib import Path
import statistics
import tempfile
import time

from fork import snap, message

with tempfile.TemporaryDirectory(prefix="pi snap benchmark ") as directory:
    source = Path(directory) / "source.jsonl"
    rows = [dict(type="session", version=3, id="bench", cwd=directory)]
    rows += [message(f"u{i}", f"u{i-1}" if i else None, "user", "x" * 100) for i in range(20000)]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    for mode in ("read+branch", "fork"):
        if mode == "fork" and not hasattr(snap, "export_fork"):
            continue
        elapsed = []
        for i in range(6):
            start = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                if mode == "fork":
                    snap.main([str(source), "u19999", "--fork", str(Path(directory) / f"fork-{i}.jsonl")])
                else:
                    entries = snap.load_entries(source)
                    snap.active_branch(entries)
            elapsed.append(time.perf_counter() - start)
        print(f"{mode}: 20,000 entries, median of 5 warm runs = {statistics.median(elapsed[1:]) * 1000:.1f} ms")
