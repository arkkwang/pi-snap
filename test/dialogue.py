"""Compact snapshot contract, stable source numbering, navigation and clocks."""
import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import re
import runpy
import tempfile
import unittest

snap = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'snap.py'))
ROW = re.compile(r'^[ ~]?\[\s*(\d+)\] ', re.M)


def row_lines(out):
    return [line for line in out.splitlines() if ROW.match(line)]


def numbers(out):
    return [int(match.group(1)) for match in ROW.finditer(out)]


def local_time(stamp):
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().strftime('%H:%M:%S')


def msg(id, parent, role, content, **extra):
    return dict(type='message', id=id, parentId=parent,
                timestamp='2026-10-10T12:00:00.123Z',
                message=dict(role=role, content=content, **extra))


def text(value):
    return dict(type='text', text=value)


class CliFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pi snap compact ')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session file.jsonl'
        self.long = 'first line\n\n' + '完整正文' * 400 + '\nlast line'
        self.rows = [
            dict(type='session', version=3, id='session', cwd='/test', timestamp='2026-10-10T12:00:00.000Z'),
            msg('sys001', None, 'system', '', toolsAdded=[dict(name='read')]),
            msg('user001', 'sys001', 'user', [text(self.long)]),
            msg('asst001', 'user001', 'assistant', [
                dict(type='thinking', thinking='THINKING BODY'), text('Explain\nthen act'),
                dict(type='toolCall', id='tc1', name='read', arguments={'path': '/file', 'offset': 10})]),
            msg('res001', 'asst001', 'toolResult', [text('RESULT BODY')], toolName='read', toolCallId='tc1'),
            msg('old001', 'res001', 'user', [text('OLD BRANCH')]),
            msg('user002', 'res001', 'user', 'Follow-up string'),
            msg('asst002', 'user002', 'assistant', [dict(type='toolCall', id='tc2', name='bash', arguments={'command': 'echo ok'})]),
            msg('res002', 'asst002', 'toolResult', [text('BASH RESULT')], toolName='bash', toolCallId='tc2'),
            msg('asst003', 'res002', 'assistant', [text('Final answer')]),
            dict(type='compaction', id='compact', parentId='asst003', summary='SUMMARY BODY'),
        ]

    def save(self):
        self.path.write_text(''.join(json.dumps(row) + '\n' for row in self.rows), encoding='utf-8')

    def run_cli(self, *args, with_session=True):
        self.save()
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


class DialogueTest(CliFixture):
    def test_readable_example_and_no_tree_or_footer(self):
        self.rows = [self.rows[0], msg('sample', None, 'user', '检查这个文件')]
        out = self.ok()
        expected = '[   2] ' + local_time(self.rows[1]['timestamp']) + ' [type:message > role:user] 检查这个文件'
        self.assertIn(expected, out.splitlines())
        for old in ('显示 ', '省略前', '条被回退', '看某条完整内容', '"message":', '时间', '.123', '落盘'):
            self.assertNotIn(old, out)
        self.assertIn('session', out)
        self.assertIn(datetime.fromisoformat(self.rows[0]['timestamp'].replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d'), out)

    def test_default_length_and_full_detail(self):
        out = self.ok()
        self.assertIn('…(+', out)
        self.assertNotIn('完整正文' * 400, out)
        user_line = next(line for line in row_lines(out) if line.startswith('[   3]'))
        self.assertRegex(user_line.split('[type:message > role:user > type:text] ', 1)[1], r'^.{300} …\(\+\d+c\)$')
        for value in ('THINKING BODY', 'RESULT BODY', 'BASH RESULT', 'SUMMARY BODY', 'Follow-up string'):
            self.assertIn(value, out)
        self.assertNotIn('OLD BRANCH', out)
        unlimited = self.ok('--max-chars', '0')
        self.assertIn(' '.join(self.long.split()), unlimited)
        self.assertNotIn('…(+', unlimited)
        self.assertEqual(json.loads(self.ok('4', '--json')), self.rows[3])
        self.assertEqual(json.loads(self.ok('asst001', '--json')), self.rows[3])
        self.assertEqual(json.loads(self.ok('1', '--json')), self.rows[0])
        self.assertIn('完整正文' * 400, self.ok('3'))

    def test_stable_numbers_branch_filter_tail_and_append(self):
        out = self.ok('--only', 'role:user')
        self.assertEqual(numbers(out), [3, 7])
        self.assertEqual(numbers(self.ok('--only', 'role:user', '--tail', '1')), [7])
        self.assertEqual(numbers(self.ok('--all', '--only', 'role:user')), [3, 6, 7])
        self.assertIn('~[   6]', self.ok('--all'))
        self.assertNotIn('asst001', self.ok())
        self.rows.append(msg('appended', 'compact', 'user', 'NEW REQUEST'))
        self.assertEqual(numbers(self.ok('--only', 'role:user')), [3, 7, 12])
        self.assertNotIn('省略', self.ok('--only', 'role:user', '--max-chars', '0'))

    def test_fixed_width_uses_whole_source_count_even_after_filter(self):
        self.rows = self.rows[:1] + [msg(f'e{i}', f'e{i-1}' if i else None, 'user', 'ROW') for i in range(10000)]
        out = self.ok('--since', '2', '--before', '3')
        self.assertEqual(row_lines(out), ['[    2] ' + local_time(self.rows[1]['timestamp'])
                                         + ' [type:message > role:user] ROW'])
        tail = self.ok('--tail', '1')
        self.assertTrue(row_lines(tail)[0].startswith('[10001] '), tail)

    def test_numbering_counts_valid_records_not_jsonl_lines(self):
        self.save()
        valid = self.path.read_text(encoding='utf8').splitlines()
        self.path.write_text('\n'.join(['', valid[0], '{bad', 'null', valid[1], *valid[2:]]), encoding='utf8')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            snap['main']([str(self.path), '3', '--json'])
        self.assertEqual(json.loads(out.getvalue()), self.rows[2])
        self.assertIn('跳过 2 行', err.getvalue())

    def test_numeric_ids_require_explicit_id_prefix(self):
        self.rows = [self.rows[0], msg('0532', None, 'user', 'NUMERIC ID'),
                     msg('2', '0532', 'user', 'OTHER NUMERIC ID')]
        self.assertEqual(json.loads(self.ok('2', '--json')), self.rows[1])
        self.assertEqual(json.loads(self.ok('id:2', '--json')), self.rows[2])
        self.assertEqual(json.loads(self.ok('id:0532', '--json')), self.rows[1])
        self.assertEqual(json.loads(self.ok('id:053', '--json')), self.rows[1])
        self.assertEqual(numbers(self.ok('--since', 'id:0532', '--before', 'id:2')), [2])
        for bad in ('0', '-1', '4', '0532'):
            self.assertNotEqual(self.run_cli(bad)[0], 0, bad)

    def test_calls_share_source_number_and_tail_counts_records(self):
        self.rows[3]['message']['content'].extend([
            dict(type='toolCall', id='tc-extra', name='read', arguments={}),
            dict(type='toolCall', id='tc-edit', name='edit', arguments={'newArg': 'FUTURE'}),
        ])
        out = self.ok('--before', '5', '--only', 'toolCall', '--tail', '1')
        self.assertEqual(numbers(out), [4, 4, 4])
        self.assertEqual(out.count('→ read'), 2)
        self.assertIn('→ edit', out)
        self.assertIn('newArg=FUTURE', out)
        self.assertEqual(set(numbers(self.ok('--only', 'toolCall', '--tail', '1'))), {8})
        self.assertEqual(set(numbers(self.ok('--tail', '2'))), {10, 11})

    def test_one_line_body_limit_not_per_string_and_safe_controls(self):
        self.rows = [self.rows[0], msg('one', None, 'user', 'line1\n\nline2\tend\x1b[31m'),
                     msg('two', 'one', 'assistant', [dict(type='toolCall', id='tc', name='new-tool',
                         arguments=dict(first='A' * 40, second='B' * 40, count=123456))])]
        unlimited = self.ok('--max-chars', '0')
        self.assertEqual(len([line for line in row_lines(unlimited) if ROW.match(line).group(1) != '1']), 2)
        self.assertIn('line1 line2', unlimited)
        self.assertNotIn('\x1b', unlimited)
        self.assertNotIn('\t', unlimited)
        self.assertIn('count=123456', unlimited)
        clipped = self.ok('--only', 'toolCall', '--max-chars', '20')
        lines = row_lines(clipped)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('[   3] '))
        self.assertIn('[type:message > role:assistant > type:toolCall]', lines[0])
        body = lines[0].split('type:toolCall] ', 1)[1]
        self.assertRegex(body, r'^.{20} …\(\+\d+c\)$')
        self.assertEqual(json.loads(self.ok('3', '--json')), self.rows[2])

    def test_known_metadata_only_in_details_unknown_fields_remain(self):
        meta = dict(api='API_ONLY', provider='PROVIDER_ONLY', model='MODEL_ONLY', usage={'cost': 'USAGE_ONLY'},
                    responseId='RESPONSE_ONLY', rawStopReason='RAWSTOP_ONLY', durationMs=987654321,
                    thinkingLevel='LEVEL_ONLY')
        self.rows = [self.rows[0], msg('meta', None, 'assistant', [
            dict(type='text', text='VISIBLE', textSignature='BLOCKSIG_ONLY', signature='TEXT_NATIVE_SIG'),
            dict(type='thinking', thinking='THOUGHT', thinkingSignature='BLOCKTHINK_ONLY', signature='THINK_NATIVE_SIG'),
            dict(type='new-block', payload='FUTURE BLOCK', textSignature='UNKNOWN_TEXTSIG',
                 thinkingSignature='UNKNOWN_THINKSIG', signature='UNKNOWN_NATIVE_SIG')], **meta,
            stopReason='aborted', errorMessage='VISIBLE ERROR', futureField='FUTURE FIELD')]
        out = self.ok('--max-chars', '0')
        for value in ('VISIBLE', 'THOUGHT', 'FUTURE BLOCK', 'futureField=FUTURE FIELD', 'aborted', 'VISIBLE ERROR',
                      'textSignature=UNKNOWN_TEXTSIG', 'thinkingSignature=UNKNOWN_THINKSIG',
                      'signature=UNKNOWN_NATIVE_SIG', 'signature=TEXT_NATIVE_SIG', 'signature=THINK_NATIVE_SIG'):
            self.assertIn(value, out)
        for value in (*meta.keys(), 'BLOCKSIG_ONLY', 'BLOCKTHINK_ONLY'):
            self.assertNotIn(value + '=' if value in meta else value, out)
        detail = self.ok('2')
        for value in (*meta.keys(), 'BLOCKSIG_ONLY', 'BLOCKTHINK_ONLY', 'TEXT_NATIVE_SIG', 'THINK_NATIVE_SIG'):
            self.assertIn(value, detail)
        self.assertEqual(json.loads(self.ok('2', '--json')), self.rows[1])

    def test_wrapped_unknown_fields_preserve_outer_and_inner_provenance(self):
        wrapped = msg('wrapped', None, 'assistant', 'BODY', future='INNER')
        wrapped['future'] = 'OUTER'
        self.rows = [self.rows[0], wrapped]
        out = self.ok('--max-chars', '0')
        self.assertIn('OUTER', out)
        self.assertIn('INNER', out)
        # Entry-level extras are grouped separately from message-level extras.
        self.assertIn('future=INNER', out)
        self.assertIn('entry=(future=OUTER)', out)
        self.assertEqual(json.loads(self.ok('2', '--json')), wrapped)

    def test_unknown_event_with_role_does_not_acquire_message_metadata_policy(self):
        event = dict(type='future_event', role='worker', id='future', parentId=None,
                     model='DOMAIN_MODEL', usage={'projectHours': 5}, content='EVENT')
        self.rows = [self.rows[0], event]
        out = self.ok('--max-chars', '0')
        for value in ('future_event', 'worker', 'model=DOMAIN_MODEL', 'usage=', 'projectHours', 'EVENT'):
            self.assertIn(value, out)
        self.assertEqual(json.loads(self.ok('2', '--json')), event)

    def test_duplicate_raw_id_branch_status_uses_source_identity(self):
        self.rows = [self.rows[0], msg('dup', None, 'user', 'OLD'), msg('dup', None, 'user', 'NEW')]
        out = self.ok('--all')
        self.assertIn('~[   2]', out)
        self.assertIn('OLD', out)
        self.assertIn('NEW', out)
        self.assertIn('已回退丢弃', self.ok('2'))
        self.assertNotIn('当前分支', self.ok('2'))
        self.assertIn('当前分支', self.ok('3'))
        self.assertEqual(json.loads(self.ok('2', '--json')), self.rows[1])
        self.assertEqual(json.loads(self.ok('3', '--json')), self.rows[2])
        self.assertNotEqual(self.run_cli('id:dup')[0], 0)

    def test_unknown_fallback_raw_values_and_end_truncation(self):
        self.rows = [self.rows[0], dict(type='future-event', id='new', parentId=None,
                    null=None, boolean=False, zero=0, empty=[], payload='LONG' * 200),
                    msg('role', 'new', 'future-role', 'FUTURE ROLE')]
        out = self.ok()
        self.assertIn('[type:future-event]', out)
        self.assertIn('null=null', out)
        self.assertIn('boolean=false', out)
        self.assertIn('zero=0', out)
        self.assertIn('empty=[]', out)
        self.assertRegex(next(line for line in row_lines(out) if '[type:future-event]' in line), r'…\(\+\d+c\)$')
        self.assertIn('[type:message > role:future-role]', out)
        self.assertIn('FUTURE ROLE', out)
        self.assertIn('LONG' * 200, self.ok('2'))

    def test_raw_status_and_interrupted_partial_blocks_not_inferred(self):
        for content in ([], [dict(type='thinking', thinking='')], [text('PARTIAL TEXT')],
                        [dict(type='toolCall', id='partial-call', name='read', arguments='PARTIAL ARGUMENTS')]):
            for reason in ('aborted', 'error', 'length'):
                record = msg('partial', 'compact', 'assistant', content,
                             stopReason=reason, errorMessage='Operation interrupted')
                self.rows = self.rows[:11] + [record]
                out = self.ok('--tail', '1')
                self.assertIn(reason, out)
                self.assertIn('Operation interrupted', out)
                self.assertNotIn('未记录结果', out)
                self.assertEqual(json.loads(self.ok('partial', '--json')), record)
        self.rows = self.rows[:11]
        self.rows[4]['message']['isError'] = True
        self.rows[8]['message']['isError'] = False
        out = self.ok()
        self.assertIn('[type:message > role:toolResult > type:text] ← read', out)
        self.assertIn('isError=true', out)
        self.assertIn('isError=false', out)

    def test_ranges_raw_ids_prefixes_and_filtered_boundaries(self):
        expected = {3, 4, 5}
        self.assertEqual(set(numbers(self.ok('--since', '3', '--before', '7'))), expected)
        self.assertEqual(set(numbers(self.ok('--since', 'user001', '--before', 'user002'))), expected)
        self.assertEqual(set(numbers(self.ok('--since', 'comp'))), {11})
        self.assertEqual(set(numbers(self.ok('--since', '3', '--before', '5', '--only', 'toolCall'))), {4})
        self.assertEqual(set(numbers(self.ok('--since', '3', '--before', '7', '--tail', '1'))), {5})
        self.assertEqual(json.loads(self.ok('old001', '--json')), self.rows[5])
        self.assertIn('已回退丢弃', self.ok('6'))

    def test_bad_bounds_conflicting_modes_and_retired_flags(self):
        for args in [('--since', 'missing'), ('--before', 'user'), ('--since', '6'),
                     ('--since', 'old001'), ('--since', '7', '--before', '3'),
                     ('--all', '--since', '3'), ('--all', '--before', '3'),
                     ('--tail', '0'), ('--tail', '-1'), ('--max-chars', '-1'),
                     ('--max-chars', '1.5'), ('--only', ''), ('--exclude', 'type:'),
                     ('--only', 'name:read'), ('--view', 'missing'), ('--view',),
                     ('--list-views', '--view', 'story'), ('--list-views', '--tail', '2'),
                     ('--json',)]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_cli(*args)[0], 0)
        for args in [('--only', 'assistant'), ('--exclude', 'thinking'), ('--view', 'trace'),
                     ('--max-chars', '10'), ('--since', '3'), ('--before', '7'), ('--tail', '2'), ('--all',)]:
            self.assertEqual(self.run_cli(*args, with_session=False)[0], 2)
            self.assertEqual(self.run_cli('4', *args)[0], 2)
            self.assertEqual(self.run_cli('3', '--fork', str(Path(self.tmp.name) / 'fork.jsonl'), *args)[0], 2)
        # --trace/--preset are retired: argparse rejects them as unknown flags.
        for retired in (('--trace',), ('--preset', 'tools'), ('--preset', 'trajectory')):
            with self.subTest(retired=retired):
                code, _, err = self.run_cli(*retired)
                self.assertEqual(code, 2)
                self.assertIn('unrecognized arguments', err)

    def test_empty_ranges_and_duplicate_id(self):
        for args in [('--since', '3', '--before', '3'), ('--only', 'absent'),
                     ('--view', 'story', '--exclude', 'role:user,role:assistant,type:text')]:
            self.assertIn('无可显示记录', self.ok(*args))
        self.rows.append(dict(self.rows[2], parentId='compact'))
        self.assertNotEqual(self.run_cli('user001')[0], 0)
        self.assertNotEqual(self.run_cli('--since', 'user001')[0], 0)

    def test_inspection_does_not_modify_source(self):
        self.save()
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            snap['main']([str(self.path), '--view', 'trace', '--max-chars', '10'])
        self.assertEqual(self.path.read_bytes(), before)


def stamp(n):
    return f'2026-10-13T00:00:{n:02d}.000Z'


def timed_fixture():
    return [dict(type='session', version=3, id='session', timestamp=stamp(0)),
            dict(type='message', id='user', parentId=None, timestamp=stamp(0),
                 message=dict(role='user', content='REQUEST')),
            dict(type='message', id='main', parentId='user', timestamp=stamp(3),
                 message=dict(role='assistant', content='MAIN RESPONSE')),
            dict(type='custom_message', customType='capability-prefetch', id='host',
                 parentId='main', timestamp=stamp(5), content='RECOMMENDATION', futureField='KEEP FUTURE',
                 details=dict(prefetch=dict(rounds=[dict(messages=[
                     dict(role='assistant', timestamp=1791849601000, content=[dict(type='toolCall', id='tc', name='read', arguments=dict(path='skill.md'))]),
                     dict(role='toolResult', timestamp=1791849602000, toolCallId='tc', toolName='read', isError=False, content='RESULT BODY'),
                 ])])))]


class TimingTests(CliFixture):
    def setUp(self):
        super().setUp()
        self.rows = timed_fixture()

    def test_actual_event_lines_same_source_number_and_full_detail(self):
        out = self.ok()
        self.assertLess(numbers(out).index(4), numbers(out).index(3))
        call = next(line for line in row_lines(out) if line.startswith('[   4] ' + local_time(stamp(1)))
                    and 'role:assistant > type:toolCall]' in line)
        result = next(line for line in row_lines(out) if line.startswith('[   4] ' + local_time(stamp(2)))
                      and 'role:toolResult]' in line)
        self.assertTrue(call.startswith('[   4] ' + local_time(stamp(1))))
        self.assertTrue(result.startswith('[   4] ' + local_time(stamp(2))))
        self.assertIn('path=skill.md', call)
        for value in ('KEEP FUTURE', 'RECOMMENDATION', 'RESULT BODY'):
            self.assertIn(value, out)
        for obsolete in ('落盘', '时间 ', '.000Z'):
            self.assertNotIn(obsolete, out)
        self.assertEqual(json.loads(self.ok('4', '--json')), self.rows[-1])
        detail = self.ok('host')
        self.assertIn('落盘 ' + stamp(5), detail)
        self.assertIn(stamp(1), detail)
        self.assertLess(numbers(self.ok('--all')).index(3), numbers(self.ok('--all')).index(4))

    def test_id_ranges_precede_reorder_and_tail_counts_source_records(self):
        self.assertNotIn(4, numbers(self.ok('--before', '3')))
        self.assertNotIn(3, numbers(self.ok('--since', 'host')))
        self.assertEqual(set(numbers(self.ok('--tail', '1'))), {3})
        self.assertEqual(set(numbers(self.ok('--only', 'customType:capability-prefetch', '--tail', '1'))), {4})

    def test_unknown_conflicts_and_barriers_do_not_invent_order(self):
        for value in (None, 'bad', 1791849609000):
            self.rows = timed_fixture()
            self.rows[-1]['details']['prefetch']['rounds'][0]['messages'][0]['timestamp'] = value
            out = self.ok()
            self.assertLess(numbers(out).index(3), numbers(out).index(4))
            self.assertIn('时间未知' if value != 1791849609000 else '时间冲突', self.ok('4'))
        for barrier in [dict(type='compaction'), dict(type='context_edit'), dict(type='branch_summary'),
                        dict(type='message', message=dict(role='user', content='NEXT')),
                        dict(type='message', message=dict(role='system', content='SYSTEM'))]:
            self.rows = timed_fixture()
            self.rows.insert(-1, dict(barrier, id='barrier', parentId='main', timestamp=stamp(4)))
            self.rows[-1]['parentId'] = 'barrier'
            out = self.ok()
            self.assertLess(numbers(out).index(3), numbers(out).index(5))

    def test_filtered_prefetch_call_retains_batch_conflict_and_own_time(self):
        self.rows[-1]['timestamp'] = stamp(4)
        self.rows[-1]['details']['prefetch']['rounds'][0]['messages'][0]['timestamp'] = 1791849609000
        out = self.ok('--only', 'toolCall')
        lines = row_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith('[   4] ' + local_time(stamp(9))), out)
        self.assertIn('role:assistant > type:toolCall] → read', lines[0])
        self.assertIn('时间冲突', lines[0])
        self.assertNotIn('RECOMMENDATION', out)
        # A conflicting batch remains after the ordinary response in the full view.
        self.assertLess(numbers(self.ok()).index(3), numbers(self.ok()).index(4))

    def test_time_windows_actual_events_whole_record_and_timezone(self):
        args = ('--since-time', stamp(1), '--before-time', stamp(3))
        out = self.ok(*args)
        self.assertEqual(set(numbers(out)), {4})
        self.assertIn('RECOMMENDATION', out)
        self.assertIn('RESULT BODY', out)
        self.assertEqual(out, self.ok('--since-time', '2026-10-13T08:00:01+08:00', '--before-time', '2026-10-13T08:00:03+08:00'))
        self.assertIn(4, numbers(self.ok('--since-time', stamp(2), '--before-time', stamp(3))))
        self.assertNotIn(4, numbers(self.ok('--since-time', stamp(4), '--before-time', stamp(6))))
        self.assertNotIn(4, numbers(self.ok('--since-time', stamp(1), '--before-time', stamp(1))))
        for args in [('--since-time', 'yesterday'), ('--since-time', '2026-10-13T00:00:00'),
                     ('--since-time', stamp(4), '--before-time', stamp(1)), ('4', '--since-time', stamp(0)),
                     ('3', '--fork', str(self.path.parent / 'fork.jsonl'), '--since-time', stamp(0))]:
            self.assertEqual(self.run_cli(*args)[0], 2)

    def test_missing_execution_time_not_replaced_by_persistence(self):
        for item in self.rows[-1]['details']['prefetch']['rounds'][0]['messages']:
            item.pop('timestamp')
        self.assertIn('执行时间未知', self.ok('4'))
        unknown_lines = [line for line in row_lines(self.ok())
                        if line.startswith('[   4] ') and 'capability-prefetch > role:' in line]
        self.assertTrue(unknown_lines)
        self.assertTrue(all(line.startswith('[   4] --:--:--') for line in unknown_lines))
        code, out, err = self.run_cli('--since-time', stamp(0), '--before-time', stamp(9))
        self.assertEqual(code, 0, err)
        self.assertNotIn(4, numbers(out))
        self.assertIn('时间未知', err)

    def test_trajectory_recommendations_and_warnings_without_results(self):
        self.rows.append(dict(type='custom_message', customType='capability-trajectory-warning',
                              id='warning', parentId='host', content='TRAJECTORY WARNING'))
        out = self.ok('--view', 'trace')
        for text in ('REQUEST', 'MAIN RESPONSE', 'RECOMMENDATION', 'skill.md', 'TRAJECTORY WARNING'):
            self.assertIn(text, out)
        self.assertNotIn('RESULT BODY', out)
        self.assertEqual(out, self.ok('--only', 'role:user,role:assistant,type:toolCall,customType:capability-prefetch,customType:capability-trajectory-warning',
                                     '--exclude', 'type:thinking,role:toolResult,type:image'))

    def test_message_clock_precision_and_cross_day_labels(self):
        self.rows = self.rows[:3]
        self.rows[-1]['message']['timestamp'] = 1791849601123
        out = self.ok('--since-time', '2026-10-13T00:00:01.100Z', '--before-time', '2026-10-13T00:00:01.124Z')
        self.assertEqual(set(numbers(out)), {3})
        self.assertIn(local_time(stamp(1)), out)
        self.assertNotIn('.123', out)
        self.assertNotIn(3, numbers(self.ok('--before-time', '2026-10-13T00:00:01.123Z')))
        self.rows[-1]['message']['timestamp'] = '2026-10-15T00:00:01.123Z'
        row = next(line for line in row_lines(self.ok()) if 'MAIN RESPONSE' in line)
        date = datetime.fromisoformat('2026-10-15T00:00:01+00:00').astimezone().strftime('%Y-%m-%d')
        self.assertIn(date, row)


class ViewAndDiscoveryFixture(CliFixture):
    """Views and type discovery on a dialogue with corrections and machinery."""

    def setUp(self):
        super().setUp()
        self.prefetch = dict(type='custom_message', customType='capability-prefetch', id='host',
                             parentId='res001', timestamp='2026-10-10T12:00:05.000Z',
                             content='RECOMMENDATION', futureField='FUTURE HOST',
                             details=dict(prefetch=dict(rounds=[dict(messages=[
                                 dict(role='assistant', timestamp='2026-10-10T12:00:05.100Z',
                                      content=[dict(type='text', text='HOST PROSE'),
                                               dict(type='toolCall', id='tc9', name='bash',
                                                    arguments=dict(command='actual-command'))]),
                                 dict(role='toolResult', timestamp='2026-10-10T12:00:05.200Z',
                                      toolCallId='tc9', toolName='bash', isError=False, content='HOST RESULT'),
                             ])])))
        self.rows = [
            dict(type='session', version=3, id='session', cwd='/test', timestamp='2026-10-10T12:00:00.000Z'),
            msg('user001', None, 'user', [text('看看这个文件')]),
            msg('asst001', 'user001', 'assistant', [
                dict(type='thinking', thinking='THINKING BODY'), text('调用前的说明'),
                dict(type='toolCall', id='tc1', name='read', arguments={'path': '/file'})]),
            msg('res001', 'asst001', 'toolResult', [text('RESULT BODY'), dict(type='image', data='IMAGE DATA')],
                toolName='read', toolCallId='tc1'),
            self.prefetch,
            msg('user002', 'host', 'user', [text('中途纠正：换个做法')]),
            msg('asst002', 'user002', 'assistant', [dict(type='toolCall', id='tc2', name='bash',
                                                         arguments={'command': 'echo hi'})]),
            msg('asst003', 'asst002', 'assistant', [text('好的，换个做法。')]),
        ]


class ViewTests(ViewAndDiscoveryFixture):
    def test_list_views_shows_both_views_and_conflicts(self):
        code, out, err = self.run_cli('--list-views', with_session=False)
        self.assertEqual(code, 0, err)
        for name in ('story', 'trace'):
            self.assertIn(name, out)
        for fragment in ('用户与 assistant', '隐藏', 'capability-prefetch', '图片'):
            self.assertIn(fragment, out)
        self.assertIn('--only', out)  # the actual selector defaults are shown too
        for args in [('--list-views', 'session'), ('--list-views', '--only', 'user'),
                     ('--list-views', '--tail', '2'), ('--list-views', '--json')]:
            self.assertEqual(self.run_cli(*args)[0], 2, args)

    def test_story_reads_dialogue_mainline_without_machinery(self):
        out = self.ok('--view', 'story')
        for keep in ('看看这个文件', '调用前的说明', '中途纠正：换个做法', '好的，换个做法。'):
            self.assertIn(keep, out)
        for hidden in ('THINKING BODY', '→ read', 'RESULT BODY', 'IMAGE DATA', 'RECOMMENDATION',
                       'HOST PROSE', 'HOST RESULT', 'actual-command', 'content=[]'):
            self.assertNotIn(hidden, out)
        # The tool-only assistant message and the session header disappear entirely.
        self.assertEqual(numbers(out), [2, 3, 6, 8])
        user_rows = [line for line in row_lines(out) if 'role:user' in line]
        self.assertTrue(all('type:message > role:user > type:text] ' in line for line in user_rows))

    def test_story_defaults_to_full_bodies_and_overrides_compose(self):
        long_body = '很长' * 500
        self.rows = [self.rows[0], msg('u', None, 'user', [text(long_body)]),
                     msg('a', 'u', 'assistant', [text('回复正文')])]
        out = self.ok('--view', 'story')
        self.assertIn(' '.join(long_body.split()), out)
        self.assertNotIn('…(+', out)
        clipped = self.ok('--view', 'story', '--max-chars', '50')
        self.assertIn('…(+', clipped)
        # --only replaces the view inclusion; shells stay suppressed by the view.
        only_user = self.ok('--view', 'story', '--only', 'role:user')
        self.assertIn(long_body, only_user)
        self.assertNotIn('回复正文', only_user)
        self.assertIn('回复正文', self.ok('--view', 'story', '--only', 'role:assistant'))
        # Custom filters without a view keep showing empty envelopes.
        custom = self.ok('--only', 'role:user,role:assistant',
                         '--exclude', 'type:thinking,type:toolCall,type:text')
        self.assertIn('content=[]', custom)

    def test_trace_keeps_dialogue_calls_and_recommendations(self):
        out = self.ok('--view', 'trace')
        for keep in ('看看这个文件', '调用前的说明', '→ read path=/file', '→ bash command=echo hi',
                     'RECOMMENDATION', 'actual-command', '好的，换个做法。'):
            self.assertIn(keep, out)
        for hidden in ('THINKING BODY', 'RESULT BODY', 'IMAGE DATA', 'HOST RESULT'):
            self.assertNotIn(hidden, out)
        # The nested preparation call keeps its host ancestry, not a fake top-level message.
        self.assertIn('type:custom_message+customType:capability-prefetch > role:assistant > type:toolCall] → bash',
                      out)
        self.assertEqual(numbers(out), [2, 3, 3, 5, 5, 5, 6, 7, 8])
        # Trace keeps the 300-character default; explicit length still overrides.
        self.rows = [self.rows[0], msg('u', None, 'user', [text('轨迹' * 400)])]
        self.assertRegex(self.ok('--view', 'trace'), r'…\(\+\d+c\)')
        self.assertIn('轨迹' * 400, self.ok('--view', 'trace', '--max-chars', '0'))


class ListTypesTests(ViewAndDiscoveryFixture):
    def test_paths_selectors_and_dedupe(self):
        out = self.ok('--list-types')
        lines = out.splitlines()
        self.assertIn('个结构标签', lines[0])
        self.assertIn('选择器用于', lines[0])
        pairs = [tuple(re.split(r'\s{2,}', line.strip())) for line in lines[1:]]
        self.assertTrue(all(len(pair) == 2 for pair in pairs), pairs)
        self.assertEqual(int(lines[0].split()[0]), len(pairs))
        for expected in (('$.type', 'type:message'), ('$.message.role', 'role:user'),
                         ('$.message.content[].type', 'type:text'),
                         ('$.message.content[].type', 'type:toolCall'),
                         ('$.message.content[].type', 'type:image'),
                         ('$.customType', 'customType:capability-prefetch'),
                         ('$.details.prefetch.rounds[].messages[].role', 'role:toolResult'),
                         ('$.details.prefetch.rounds[].messages[].content[].type', 'type:toolCall')):
            self.assertIn(expected, pairs)
        # A listed qualified selector really selects the subtree it came from.
        picked = self.ok('--only', 'type:toolCall', '--tail', '1')
        self.assertIn('→ bash command=echo hi', picked)
        # Prose stays prose: the recommendation text never becomes an event tag.
        self.assertNotIn('RECOMMENDATION', out)

    def test_branch_default_all_and_ranges(self):
        self.rows = self.rows + [
            msg('old', 'asst003', 'user', [dict(type='legacy-block', payload='OLD BRANCH')]),
            msg('user003', 'asst003', 'user', [text('当前分支新消息')])]
        branch = self.ok('--list-types')
        self.assertNotIn('type:legacy-block', branch)
        self.assertIn('type:legacy-block', self.ok('--all', '--list-types'))
        self.assertNotIn('role:user', self.ok('--list-types', '--since', '3', '--before', '3'))
        self.assertIn('role:toolResult', self.ok('--list-types', '--since', 'host', '--before', 'user002'))
        self.assertIn('role:user', self.ok('--list-types', '--since-time', '2026-10-10T12:00:00.123Z',
                                           '--before-time', '2026-10-10T12:00:00.124Z'))
        # Discovery precedes filtering: types hidden by a view stay discoverable.
        self.assertNotIn('IMAGE DATA', self.ok('--view', 'story'))
        self.assertIn('type:image', self.ok('--list-types'))
        for args in [('--view', 'story'), ('--only', 'user'), ('--exclude', 'thinking'),
                     ('--max-chars', '10'), ('--tail', '2'), ('2',), ('2', '--json')]:
            self.assertEqual(self.run_cli('--list-types', *args)[0], 2, args)


if __name__ == '__main__':
    unittest.main()
