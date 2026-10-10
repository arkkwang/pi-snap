"""Dialogue-first navigation and stable-ID execution ranges (no private logs)."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest

snap = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'snap.py'))


def msg(id, parent, role, content, **extra):
    return dict(type='message', id=id, parentId=parent,
                timestamp='2026-10-10T12:00:00Z',
                message=dict(role=role, content=content, **extra))


def text(value):
    return dict(type='text', text=value)


class DialogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pi snap dialogue ')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session file.jsonl'
        self.long = 'first line\n\n' + '完整正文' * 400 + '\nlast line'
        self.rows = [
            dict(type='session', version=3, id='session', cwd='/test'),
            msg('sys001', None, 'system', '', toolsAdded=[dict(name='read')]),
            msg('user001', 'sys001', 'user', [text(self.long)]),
            msg('asst001', 'user001', 'assistant', [
                dict(type='thinking', thinking='HIDDEN THINKING'), text('Explain\nthen act'),
                dict(type='toolCall', id='tc1', name='read', arguments={'path': '/file'})]),
            msg('res001', 'asst001', 'toolResult', [text('RESULT BODY')], toolName='read', toolCallId='tc1'),
            msg('old001', 'res001', 'user', [text('OLD BRANCH')]),
            msg('user002', 'res001', 'user', 'Follow-up string'),
            msg('asst002', 'user002', 'assistant', [dict(type='toolCall', id='tc2', name='bash', arguments={'command': 'echo ok'})]),
            msg('res002', 'asst002', 'toolResult', [text('BASH RESULT')], toolName='bash', toolCallId='tc2'),
            msg('asst003', 'res002', 'assistant', [text('Final answer')]),
            dict(type='compaction', id='compact', parentId='asst003', summary='HIDDEN SUMMARY'),
        ]
        self.save()

    def save(self):
        self.path.write_text(''.join(json.dumps(row) + '\n' for row in self.rows), encoding='utf-8')

    def run_cli(self, *args, with_session=True):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                snap['main'](([str(self.path)] if with_session else []) + list(args))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args):
        code, out, err = self.run_cli(*args)
        self.assertEqual(code, 0, err + out)
        return out

    def test_default_full_text_and_stable_ids(self):
        out = self.ok()
        self.assertIn(self.long, out)
        self.assertIn('[asst001]', out)
        self.assertIn('Explain\nthen act', out)
        self.assertIn('Follow-up string', out)
        for hidden in ('[→', '[←', '[system]', '[status]', 'HIDDEN', 'RESULT BODY', 'OLD BRANCH', '[asst002]'):
            self.assertNotIn(hidden, out)
        self.assertIn('--trace', out)
        self.assertIn('[asst001]', self.ok('--trace'))
        self.assertEqual(json.loads(self.ok('asst001', '--json')), self.rows[3])

    def test_tool_counts_are_grouped_between_texts_and_not_results(self):
        out = self.ok()
        self.assertEqual(out.count('read × 1'), 1)
        self.assertEqual(out.count('bash × 1'), 1)
        self.assertNotIn('read × 2', out)
        self.assertLess(out.index('Explain\nthen act'), out.index('read × 1'))
        self.assertLess(out.index('read × 1'), out.index('Follow-up string'))
        self.assertLess(out.index('bash × 1'), out.index('Final answer'))
        self.assertNotIn('↳ 工具调用', self.ok('--trace'))
        self.assertNotIn('bash ×', self.ok('--tail', '1'))
        ranged = self.ok('--since', 'asst002', '--before', 'asst003')
        self.assertIn('bash × 1', ranged)
        self.assertNotIn('read ×', ranged)

    def test_multiple_calls_in_one_entry_count_individually(self):
        self.rows[3]['message']['content'].extend([
            dict(type='toolCall', id='tc-extra', name='read', arguments={}),
            dict(type='toolCall', id='tc-edit', name='edit', arguments={}),
        ])
        self.save()
        out = self.ok('--before', 'user002')
        self.assertIn('read × 2 · edit × 1', out)
        self.assertEqual(out.count('↳ 工具调用'), 1)
        self.assertNotIn('bash ×', out)

    def test_tail_counts_visible_entries_not_tool_rows_or_metadata(self):
        out = self.ok('--tail', '2')
        self.assertIn('[user002]', out)
        self.assertIn('[asst003]', out)
        self.assertNotIn('[asst001]', out)
        self.assertIn('省略前 2 条', out)
        trace = self.ok('--trace', '--tail', '2')
        self.assertIn('[asst003]', trace)
        self.assertIn('[compact]', trace)
        self.assertNotIn('[res002]', trace)

    def test_range_inclusive_since_exclusive_before_and_prefix(self):
        out = self.ok('--trace', '--since', 'user001', '--before', 'user002')
        for id in ('user001', 'asst001', 'res001'):
            self.assertIn('[' + id + ']', out)
        for id in ('sys001', 'old001', 'user002', 'asst002'):
            self.assertNotIn('[' + id + ']', out)
        self.assertIn('[→ read]', out)
        self.assertIn('[← read]', out)
        self.assertNotIn('未记录结果', out)
        out = self.ok('--since', 'res001', '--before', 'asst002')
        self.assertIn('[user002]', out)
        self.assertNotIn('[res001]', out)
        self.assertNotIn('[asst003]', out)
        self.assertIn('[compact]', self.ok('--trace', '--since', 'compact'))
        self.assertNotIn('[user002]', self.ok('--before', 'user002'))

    def test_trace_range_keeps_prior_tool_inventory_and_call_pairing(self):
        self.rows.append(msg('sys002', 'compact', 'system', '', toolsRemoved=['read']))
        self.save()
        out = self.ok('--trace', '--since', 'sys002')
        self.assertIn('Tools=0(-1)', out)
        out = self.ok('--trace', '--since', 'asst001', '--before', 'res001')
        self.assertNotIn('未记录结果', out)  # result exists outside selected window
        self.assertIn('区间', out)

    def test_range_then_tail(self):
        out = self.ok('--trace', '--since', 'user001', '--before', 'user002', '--tail', '1')
        self.assertIn('[res001]', out)
        self.assertNotIn('[asst001]', out)
        self.assertNotIn('[user002]', out)

    def test_all_keeps_original_branch_marks(self):
        out = self.ok('--all')
        self.assertIn('~[old001]', out)
        self.assertNotIn('[←', out)
        self.assertIn('~[old001]', self.ok('--all', '--trace'))

    def test_bad_bounds_and_conflicting_modes(self):
        for args in [('--since', 'missing'), ('--before', 'user'),
                     ('--since', 'old001'), ('--since', 'user002', '--before', 'user001'),
                     ('--all', '--since', 'user001'), ('--tail', '0'), ('--tail', '-1'),
                     ('asst001', '--trace'), ('asst001', '--since', 'user001'),
                     ('asst001', '--fork', str(Path(self.tmp.name) / 'fork.jsonl'), '--trace')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_cli(*args)[0], 0)
        for args in [('--trace',), ('--since', 'user001'), ('--before', 'user002'), ('--tail', '2')]:
            self.assertNotEqual(self.run_cli(*args, with_session=False)[0], 0)

    def test_empty_range_and_empty_dialogue_are_explicit(self):
        self.assertIn('无可显示记录', self.ok('--since', 'user001', '--before', 'user001'))
        self.assertIn('无可显示记录', self.ok('--since', 'asst002', '--before', 'asst003'))

    def test_duplicate_exact_id_rejected(self):
        self.rows.append(dict(self.rows[2], parentId='compact'))
        self.save()
        self.assertNotEqual(self.run_cli('user001')[0], 0)
        self.assertNotEqual(self.run_cli('--since', 'user001')[0], 0)

    def test_image_only_user_preserves_turn_placeholder(self):
        self.rows.append(msg('image001', 'compact', 'user', [dict(type='image', data='HIDDEN IMAGE')]))
        self.save()
        out = self.ok('--tail', '1')
        self.assertIn('[image001]', out)
        self.assertIn('[image]', out)
        self.assertNotIn('HIDDEN IMAGE', out)


if __name__ == '__main__':
    unittest.main()
