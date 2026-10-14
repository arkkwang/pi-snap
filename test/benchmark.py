"""20,000-record core benchmark: python3 test/benchmark.py (synthetic only).

Complements trajectory-benchmark.mjs: this measures bulk load/selection/export,
not process startup. Views and explicit filters use the same renderer.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import statistics
import re
import tempfile
import time

spec = importlib.util.spec_from_file_location('snap', Path(__file__).resolve().parents[1] / 'snap.py')
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)

with tempfile.TemporaryDirectory(prefix='pi snap benchmark ') as directory:
    source = Path(directory) / 'source.jsonl'
    rows = [dict(type='session', version=3, id='bench', cwd=directory)]
    for i in range(20000):
        role = 'user' if i % 2 == 0 else 'assistant'
        content = 'x' * 100 if role == 'user' else [
            dict(type='thinking', thinking='t' * 200), dict(type='text', text='x' * 100),
            dict(type='future-block', data=[None, False, i])]
        rows.append(dict(type='message', id=f'e{i}', parentId=f'e{i-1}' if i else None,
                         message=dict(role=role, content=content)))
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    for mode, options in [
        ('read+branch', None),
        ('default+tail20', snap.resolve_options()),
        ('story+tail20', snap.resolve_options('story')),
        ('trace+tail20', snap.resolve_options('trace')),
        ('filtered+limited+tail20', snap.resolve_options(only=['type:text'], max_chars=30)),
        ('fork', None),
    ]:
        elapsed = []
        for i in range(6):
            start = time.perf_counter()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                if mode == 'fork':
                    snap.main([str(source), 'e19999', '--fork', str(Path(directory) / f'fork-{i}.jsonl')])
                else:
                    entries = snap.load_entries(source)
                    if options is not None:
                        snap.render_snapshot(entries, str(source), False, 20, options=options)
                    else:
                        chain, _ = snap.active_branch(entries)
            elapsed.append(time.perf_counter() - start)
            if mode == 'read+branch':
                assert len(chain) == 20000
            elif options is not None:
                numbers = {int(n) for n in re.findall(r'^\[\s*(\d+)\]', out.getvalue(), re.M)}
                assert len(numbers) == 20 and 20001 in numbers
        print(f'{mode}: 20,000 entries, median of 5 warm runs = {statistics.median(elapsed[1:]) * 1000:.1f} ms')
