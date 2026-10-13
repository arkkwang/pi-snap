"""Default trajectory navigation and stable-ID execution ranges (no private logs)."""
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
        for hidden in ('[status]', 'HIDDEN', 'RESULT BODY', 'BASH RESULT', 'OLD BRANCH'):
            self.assertNotIn(hidden, out)
        self.assertIn('--trace', out)
        self.assertIn('[asst001]', self.ok('--trace'))
        self.assertEqual(json.loads(self.ok('asst001', '--json')), self.rows[3])

    def test_calls_and_status_are_in_order_without_result_bodies(self):
        out = self.ok()
        self.assertEqual(out.count('[→ read]'), 1)
        self.assertEqual(out.count('[→ bash]'), 1)
        self.assertIn('[← read]', out)
        self.assertIn('[system]', out)
        self.assertLess(out.index('Explain\nthen act'), out.index('[→ read]'))
        self.assertLess(out.index('[→ read]'), out.index('Follow-up string'))
        self.assertLess(out.index('[→ bash]'), out.index('Final answer'))
        self.assertNotIn('↳ 工具调用', out)
        ranged = self.ok('--since', 'asst002', '--before', 'asst003')
        self.assertIn('[→ bash]', ranged)
        self.assertNotIn('[→ read]', ranged)

    def test_failed_results_surface_and_successful_results_stay_hidden(self):
        self.rows[4]['message']['isError'] = True   # res001 失败
        self.rows[8]['message']['isError'] = False  # res002 成功
        self.save()
        out = self.ok()
        self.assertIn('[← read]', out)
        self.assertIn('[← read] ERR', out)
        self.assertNotIn('[← bash]', out)
        self.assertIn('[← bash] ok', self.ok('--trace'))
        tail = self.ok('--tail', '3')
        self.assertIn('[asst003]', tail)
        self.assertNotIn('[res002]', tail)
        self.assertIn('省略前 5 条', tail)  # 可见 8 条：成功结果不计入

    def test_host_bookkeeping_stays_out_of_trajectory(self):
        self.rows.append(msg('fail1', 'compact', 'assistant', [],
                             stopReason='error', errorMessage='fetch failed'))
        self.rows.append(dict(type='context_edit', id='ctx1', parentId='fail1',
                              targetId='fail1', replacement=None))
        self.save()
        out = self.ok()
        self.assertNotIn('[fail1]', out)
        self.assertNotIn('[ctx1]', out)
        self.assertNotIn('fetch failed', out)
        trace = self.ok('--trace')
        self.assertIn('响应失败', trace)
        self.assertIn('fetch failed', trace)
        self.assertIn('[ctx1]', trace)
        self.assertIn('fetch failed', self.ok('fail1'))  # 单条展开仍可查
        self.assertIn('context_edit', self.ok('ctx1'))

    def test_multiple_calls_in_one_entry_share_id_and_tail_count(self):
        self.rows[3]['message']['content'].extend([
            dict(type='toolCall', id='tc-extra', name='read', arguments={}),
            dict(type='toolCall', id='tc-edit', name='edit', arguments={}),
        ])
        self.save()
        out = self.ok('--before', 'res001', '--tail', '1')
        self.assertEqual(out.count('[→ read]'), 2)
        self.assertEqual(out.count('[→ edit]'), 1)
        self.assertNotIn('[user001]', out)
        self.assertNotIn('[→ bash]', out)

    def test_tail_counts_visible_entries_including_tools_and_events(self):
        for args in [('--tail', '2'), ('--trace', '--tail', '2')]:
            out = self.ok(*args)
            self.assertIn('[asst003]', out)
            self.assertIn('[compact]', out)
            self.assertNotIn('[res002]', out)
            self.assertIn('省略前 7 条', out)

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
        self.assertIn('[res001]', out)
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
        self.assertIn('[←', out)
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
        self.assertIn('[→ bash]', self.ok('--since', 'asst002', '--before', 'asst003'))

    def test_duplicate_exact_id_rejected(self):
        self.rows.append(dict(self.rows[2], parentId='compact'))
        self.save()
        self.assertNotEqual(self.run_cli('user001')[0], 0)
        self.assertNotEqual(self.run_cli('--since', 'user001')[0], 0)

    def test_image_only_user_keeps_placeholder_without_payload(self):
        self.rows.append(msg('image001', 'compact', 'user', [dict(type='image', data='HIDDEN IMAGE')]))
        self.save()
        out = self.ok('--tail', '1')
        self.assertIn('[image001]', out)
        self.assertIn('[image]', out)
        self.assertIn('[image]', self.ok('--trace', '--tail', '1'))
        self.assertNotIn('HIDDEN IMAGE', out)


if __name__ == '__main__':
    unittest.main()
