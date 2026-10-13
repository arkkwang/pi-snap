"""Snapshot limits, system summaries and branch-local tool inventories."""
import contextlib
import io
import runpy
from pathlib import Path

api = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'snap.py'))


def entry(id, parent=None, **message):
    return dict(type='message', id=id, parentId=parent, message=message)


def render(e):
    return '\n'.join(api['render_message'](e, '', None, {}, set()))


def call(name, text):
    args = {'command': text} if name == 'bash' else {'query': text}
    return entry('call', role='assistant', content=[
        dict(type='toolCall', id='tc', name=name, arguments=args)])


for size in (599, 600, 601):
    out = render(call('bash', 'x' * size))
    assert ('…(+' in out) == (size > 600), out
    if size > 600:
        assert 'x' * 600 + ' …(+1c)' in out
assert '…(+' in render(call('search', 'x' * 200))
# Configuration alone changes limits, including tools with no special renderer.
api['TOOL_SUMMARIES']['bash']['max_chars'] = 400
api['TOOL_SUMMARIES']['search'] = {'max_chars': 600}
assert 'x' * 400 + ' …(+1c)' in render(call('bash', 'x' * 401))
assert '…(+' not in render(call('search', 'x' * 200))
api['TOOL_SUMMARIES']['bash']['max_chars'] = 600

# Selection, composition, derived values and conditional segments are all configured.
fmt = api['fmt_call_args']
for name, args, expected in [
    ('bash', {'command': 'echo   hi\nthere', 'timeout': 30}, 'echo hi there'),
    ('bash', {}, ''),
    ('read', {'path': '/a'}, '/a'),
    ('read', {'path': '/a', 'offset': 0}, '/a @0+?'),
    ('read', {'path': '/a', 'limit': 50}, '/a @?+50'),
    ('read', {'path': '/a', 'offset': None, 'limit': None}, '/a'),
    ('read', {'path': '/a', 'offset': None, 'limit': 50}, '/a @?+50'),
    ('read', {'path': '/a', 'offset': 0, 'limit': None}, '/a @0+?'),
    ('read', {'path': '/a', 'offset': None}, '/a'),
    ('ls', {'path': '/a'}, '/a'),
    ('write', {'path': '/a', 'content': '字' * 1200}, '/a · 1.2Kc'),
    ('write', {'path': '/a', 'content': None}, '/a · 0c'),
    ('edit', {'path': '/a', 'edits': [{}, {}]}, '/a · 2 处修改'),
    ('edit', {'path': '/a'}, '/a · 0 处修改'),
    ('grep', {'pattern': '字.*', 'path': '/a'}, '字.* · /a'),
    ('find', {'pattern': '*.md', 'path': '/a'}, '*.md · /a'),
    ('unknown', {'query': '字'}, '{"query": "字"}'),
    ('unknown', None, 'null'),
    ('unknown', ['字', 1], '["字", 1]'),
    ('unknown', 'partial  arguments', 'partial arguments'),
    ('bash', 'partial  arguments', 'partial arguments'),
]:
    assert fmt(name, args) == expected, (name, fmt(name, args), expected)
api['TOOL_SUMMARIES']['search'] = {
    'max_chars': 400,
    'template': '{query} · {tags:count}',
    'optional': [{'when_any': ('path',), 'template': ' in {path}'}],
}
assert fmt('search', {'query': 'find me', 'tags': ['a', 'b']}) == 'find me · 2'
assert fmt('search', {'query': 'find me', 'path': '/a'}) == 'find me · 0 in /a'
assert 'x' * 400 + ' …(+5c)' in render(call('search', 'x' * 401))
# Restore config to keep following checks independent.
del api['TOOL_SUMMARIES']['search']
# Middle truncation is display-only and independent of the host path syntax.
for path in [
    '/Users/name/' + 'directory/' * 25 + '笔记.md',
    'C:\\Users\\name\\' + 'directory\\' * 25 + 'notes.md',
    '//server/share/' + 'directory/' * 25 + 'notes.md',
    '~/my project/' + 'directory/' * 25 + 'notes.md',
]:
    for name, extra, suffix in [
        ('read', {'offset': 0, 'limit': 50}, ' @0+50'),
        ('write', {'content': 'hello'}, ' · 5c'),
        ('edit', {'edits': [{}]}, ' · 1 处修改'),
        ('ls', {}, ''),
    ]:
        e = entry('path', role='assistant', content=[dict(
            type='toolCall', id='tc', name=name, arguments={'path': path, **extra})])
        out = render(e)
        full = fmt(name, {'path': path, **extra})
        assert full[:40] in out and full[-80:] in out, out
        assert f'…(+{len(full) - 120}c)' in out, out
        assert path not in out
        assert suffix in out
assert api['trunc']('abcdef', 3, 'middle') == 'a …(+3c) ef'
assert api['trunc']('abc', 3, 'middle') == 'abc'
assert api['trunc']('abc', 1, 'middle') == ' …(+2c) c'
assert api['trunc']('abc', 0, 'middle') == ' …(+3c)'
# Extremely long filenames still retain their extension; full details are untouched.
assert api['trunc']('/a/' + 'x' * 200 + '.md', 120, 'middle').endswith('.md')
for role, limit in [('user', 800), ('assistant', 600)]:
    out = render(entry('text', role=role, content=[dict(type='text', text='字' * (limit + 1))]))
    assert '字' * limit + ' …(+1c)' in out
out = render(entry('result', role='toolResult', toolName='bash',
                   content=[dict(type='text', text='x' * 101)]))
assert 'x' * 100 + ' …(+1c)' in out

# Unconfigured results keep the default metadata, error status, duration and images.
def result(name, text, error=False):
    return entry('result', role='toolResult', toolName=name, isError=error,
                 toolCallId='tc', content=[dict(type='text', text=text), dict(type='image')])

out = render(result('unknown', '字' * 101))
assert '[← unknown] ok 101c' in out and '字' * 100 + ' …(+1c)' in out and '[image]' in out
assert 'ERR 101c' in render(result('unknown', '字' * 101, True))
r = result('unknown', 'hello')
r['timestamp'] = '2026-10-09T00:00:02Z'
out = '\n'.join(api['render_message'](r, '', None, {'tc': api['parse_ts']('2026-10-09T00:00:00Z')}, set()))
assert 'ok 5c 2s' in out
assert 'line1' not in render(result('read', 'line1\nline2'))
assert 'ok 2 lines 11c' in render(result('read', 'line1\nline2'))
assert 'line1' in render(result('read', 'line1\nline2', True))
for text, lines in [('', 0), ('a', 1), ('a\n', 1), ('a\nb', 2),
                    ('a\nb\n', 2), ('\n', 1), ('a\n\n', 2), ('a\r\nb\r\n', 2)]:
    assert f'ok {lines} lines ' in render(result('read', text)), (repr(text), lines)
api['TOOL_SUMMARIES']['custom'] = {'result': {
    'template': '{status} {lines} rows {size}{duration}',
    'preview_chars': 3,
    'error_preview_chars': 5,
}}
assert 'ok 1 rows 6c  abc …(+3c)' in render(result('custom', 'abcdef'))
assert 'ERR 1 rows 6c  abcde …(+1c)' in render(result('custom', 'abcdef', True))
assert fmt('custom', {'key': 'value'}) == '{"key": "value"}'
api['TOOL_SUMMARIES']['custom']['result']['preview_chars'] = 0
assert 'abcdef' not in render(result('custom', 'abcdef'))
assert ' …(+' not in render(result('custom', 'abcdef'))
assert '[image]' in render(result('custom', ''))
del api['TOOL_SUMMARIES']['custom']

sections = dict(cwd='<cwd>\n/path with spaces/项目\n</cwd>',
                skills='<available_skills><skill>one</skill><skill>two</skill></available_skills>',
                mcp_servers='<mcp_servers>\n- mcp__exa (tool_search): search\n- mcp__browser (tool_search): browse\n</mcp_servers>',
                project_context='<project_instructions path="rules">SECRET</project_instructions>')
s1 = entry('s1', role='system', sections=sections, toolsAdded=[{'name': 'bash'}, {'name': 'read'}])
a = entry('a', 's1', role='assistant', content=[])
s2 = entry('s2', 'a', role='system', toolsAdded=[{'name': 'edit'}], toolsRemoved=['read'])
s3 = entry('s3', 'a', role='system', toolsAdded=[{'name': 'write'}])
s4 = entry('s4', 's3', role='system', toolsRemoved=['bash'])
# Real Pi sessions persist removals as full tool objects, not bare names.
s5 = entry('s5', 's4', role='system', toolsRemoved=[{'name': 'write', 'description': 'write files'}])


def snapshot(entries, all=False, tail=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        api['render_snapshot'](entries, 'fixture', all, tail, trace=True)
    return out.getvalue()


entries = [s1, a, s2, s3, s4, s5]
out = snapshot(entries, all=True)
assert 'cwd=/path with spaces/项目 · Skills=2 · MCP=2 · Tools=2 · Instructions=1' in out
assert 'SECRET' not in out
assert '~[s2] [system]  Tools=2(+1/-1)' in out
assert '[s3] [system]  Tools=3(+1)' in out
assert '[s4] [system]  Tools=2(-1)' in out
assert '[s5] [system]  Tools=1(-1)' in out
assert len([line for line in out.splitlines() if '[system]' in line]) == 5
out = snapshot(entries, tail=1)
assert '[s1]' not in out and '[s5] [system]  Tools=1(-1)' in out
assert '~[s2]' not in snapshot(entries)
assert 'Tools=?(-1)' in render(entry('unknown', role='system', toolsRemoved=['bash']))
assert 'sections=custom' in render(entry('custom', role='system', sections={'custom': 'SECRET'}))
assert 'Prompt=3c' in render(entry('plain', role='system', content='abc'))
assert 'Prompt=5c' in render(entry('combined', role='system',
    sections={'preamble': '中文'}, content=[dict(type='text', text='abc')],
    toolsAdded=[{'name': 'tool', 'description': 'not counted' * 100}]))
assert 'Prompt=1.2Kc' in render(entry('large', role='system', sections={'preamble': '字' * 1200}))
assert f'Prompt={api["fmt_size"](sum(map(len, sections.values())))}' in snapshot(entries)
assert '(empty)' in render(entry('empty', role='system'))
print('PASS: input and result templates / configurable limits / unknown tools / one-line system / branch-local tools / tail')
