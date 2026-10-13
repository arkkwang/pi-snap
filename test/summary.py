"""Generic structural filtering/rendering regressions (replaces tool templates)."""
import contextlib
import copy
import io
from pathlib import Path
import runpy
import unittest

api = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'snap.py'))


def select(record, only=None, exclude=None):
    options = api['resolve_options'](only=only, exclude=exclude)
    return api['filter_record'](record, options['only'], options['exclude'])


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(type='message', id='outer', parentId=None, timestamp='source time',
                           envelopeExtra='UNRELATED ENVELOPE', message=dict(
            role='assistant', timestamp=123, usage={'tokens': 100}, content=[
                dict(type='thinking', thinking='THOUGHT'),
                dict(type='text', text='TEXT'),
                dict(type='toolCall', id='tc', name='unknown-tool', arguments={
                    'payload': 'PRESERVE', 'raw': [None, False, 0, '', [], {}],
                    'nested': {'customType': 'vendor:event', 'value': 'CUSTOM'}}),
                dict(type='future-block', value='FUTURE')]))

    def test_no_filter_preserves_every_field_and_raw_value(self):
        self.assertEqual(select(self.record), self.record)
        for value in (None, False, 0, '', [], {}, [None, False, 0, '', [], {}]):
            with self.subTest(value=value):
                record = dict(type='future-event', role='future-role', payload=value)
                self.assertEqual(select(record), record)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            api['render_entry_full'](self.record, True, False)
        for token in ('THOUGHT', 'FUTURE', 'PRESERVE', 'usage', 'UNRELATED ENVELOPE',
                      'null', 'false', '0', '""', '[]', '{}', 'unknown-tool'):
            self.assertIn(token, out.getvalue())

    def test_only_descendant_retains_path_and_context_not_sibling_payload(self):
        expected = {key: self.record[key] for key in ('type', 'id', 'parentId', 'timestamp')}
        expected['message'] = dict(role='assistant', timestamp=123,
                                   content=[self.record['message']['content'][2]])
        self.assertEqual(select(self.record, ['toolCall']), expected)
        self.assertEqual(select(self.record, ['type:toolCall']), expected)
        nested = select(self.record, ['customType:vendor:event'])
        call = nested['message']['content'][0]
        self.assertEqual(call, dict(type='toolCall', id='tc', arguments={
            'nested': {'customType': 'vendor:event', 'value': 'CUSTOM'}}))
        self.assertNotIn('envelopeExtra', nested)
        self.assertNotIn('usage', nested['message'])

    def test_include_opens_subtree_exclude_wins_at_any_depth(self):
        projected = select(self.record, ['role:assistant'], ['thinking,customType:vendor:event'])
        self.assertEqual(projected['message']['usage'], {'tokens': 100})
        parts = projected['message']['content']
        self.assertEqual([part['type'] for part in parts], ['text', 'toolCall', 'future-block'])
        self.assertNotIn('nested', parts[1]['arguments'])
        self.assertEqual(parts[1]['arguments']['raw'], [None, False, 0, '', [], {}])
        self.assertIs(select(self.record, ['toolCall'], ['role:assistant']), api['OMIT'])
        self.assertIs(select(self.record, ['message'], ['message']), api['OMIT'])
        # Exclusion-only preserves surrounding data, including empty containers.
        projected = select(self.record, exclude=['thinking,text,toolCall,future-block'])
        self.assertEqual(projected['message']['content'], [])
        self.assertEqual(projected['envelopeExtra'], 'UNRELATED ENVELOPE')

    def test_matching_is_exact_case_sensitive_string_tag_only(self):
        rows = [dict(type='wanted', value=1), dict(role='wanted', value=2),
                dict(customType='wanted', value=3), dict(type='Wanted', value=4),
                dict(type='wanted-more', value=5), dict(type=['wanted'], value=6),
                dict(type=1, value=7), dict(name='wanted', value=8)]
        self.assertEqual(select({'rows': rows}, ['wanted']), {'rows': rows[:3]})
        for field, index in [('type', 0), ('role', 1), ('customType', 2)]:
            self.assertEqual(select({'rows': rows}, [field + ':wanted']), {'rows': [rows[index]]})
        self.assertIs(select({'rows': rows}, ['1']), api['OMIT'])
        self.assertIs(select({'rows': rows}, ['want']), api['OMIT'])

    def test_repeated_comma_selectors_and_no_mutation(self):
        original = copy.deepcopy(self.record)
        projected = select(self.record, [' thinking , type:text', 'type:future-block'])
        self.assertEqual([p['type'] for p in projected['message']['content']],
                         ['thinking', 'text', 'future-block'])
        select(self.record, exclude=['thinking', 'toolCall,text'])
        self.assertEqual(self.record, original)
        for bad in ('', 'a,', ',a', 'a,,b', 'name:tool', 'type:', ':value'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                api['parse_selectors']([bad])

    def test_presets_are_only_configuration_and_overrides_compose(self):
        self.assertEqual(set(api['PRESETS']), {'all', 'conversation', 'tools', 'trajectory'})
        for name, base in api['PRESETS'].items():
            with self.subTest(preset=name):
                self.assertEqual(set(base), {'only', 'exclude', 'max_chars'})
                self.assertEqual(base['max_chars'], 300)
                self.assertEqual(api['resolve_options'](name), api['resolve_options'](
                    only=base['only'], exclude=base['exclude'], max_chars=base['max_chars']))
        opts = api['resolve_options']('conversation', ['customType:new'], ['type:image'], 7)
        self.assertEqual(opts['only'], (('customType', 'new'),))
        self.assertEqual(opts['exclude'], (('type', 'thinking'), ('type', 'toolCall'), ('type', 'image')))
        self.assertEqual(opts['max_chars'], 7)
        # Prove max_chars defaults are configuration, not hard-coded preset branches.
        api['PRESETS']['all']['max_chars'] = 9
        try:
            self.assertEqual(api['resolve_options']()['max_chars'], 9)
            self.assertEqual(api['resolve_options'](max_chars=0)['max_chars'], 0)
        finally:
            api['PRESETS']['all']['max_chars'] = 300

    def test_full_detail_keeps_strings_and_all_metadata(self):
        record = dict(type='future-event', id='long-navigation-id', long_field_name='中文' * 1000,
                      second='uvwxyz', number=123456, boolean=False, null=None,
                      message=dict(role='assistant', usage={'input': 10}, textSignature='SIGNATURE'))
        original = copy.deepcopy(record)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            api['render_entry_full'](record, True, False)
        for token in ('中文' * 1000, 'long_field_name', '123456', 'false', 'null', 'SIGNATURE', 'usage'):
            self.assertIn(token, out.getvalue())
        self.assertEqual(record, original)

    def test_multiline_and_control_characters_are_not_terminal_commands(self):
        out = '\n'.join(api['value_lines']({'text': 'first\n\nlast\t\x1b[31m\r'}))
        self.assertIn('"text": |\n', out)
        self.assertIn('first\n', out)
        self.assertIn('last\\t\\u001b[31m\\r', out)
        self.assertNotIn('\x1b', out)
        self.assertNotIn('\r', out)


if __name__ == '__main__':
    unittest.main()
