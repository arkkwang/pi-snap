// npm launcher acceptance: compact rows, composable presets and raw evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
const msg = (id, message) => ({ type: 'message', id, message });
const call = (name, args, id = 'call') => ({ type: 'toolCall', id, name, arguments: args });
const sourceRows = entries => entries.map((entry, i) => ({ parentId: i ? entries[i - 1].id : null, ...entry }));
const invoke = (entries, args = []) => {
  const dir = mkdtempSync(path.join(tmpdir(), 'pi snap compact '));
  try {
    const file = path.join(dir, 'session file.jsonl');
    writeFileSync(file, sourceRows(entries).map(e => JSON.stringify(e)).join('\n'));
    return spawnSync(process.execPath, [cli, file, ...args], { encoding: 'utf8' });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
};
const output = (entries, args = []) => {
  const result = invoke(entries, args);
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  return result.stdout;
};
const rows = out => out.split(/\r?\n/).filter(line => /^~?\[\s*\d+\] /.test(line));
const fixture = () => [
  { type: 'session', version: 3, id: 'session', timestamp: '2026-10-13T00:00:00Z' },
  msg('u', { role: 'user', content: 'REQUEST' }),
  msg('a', { role: 'assistant', stopReason: 'aborted', usage: { tokens: 9 }, content: [
    { type: 'thinking', thinking: 'THOUGHT' }, { type: 'text', text: 'RESPONSE' },
    call('delegate', { task: 'work', content: 'PAYLOAD', model: 'model-name', nested: { data: 'DATA' } }),
    { type: 'future-block', payload: 'FUTURE BLOCK' },
  ] }),
  msg('r', { role: 'toolResult', toolCallId: 'call', toolName: 'delegate', isError: false,
    content: [{ type: 'text', text: 'RESULT BODY' }, { type: 'image', data: 'IMAGE DATA' }] }),
  { type: 'custom', customType: 'capability-prefetch-diagnostic', id: 'diag', data: {
    configuredOptions: { trajectoryWarnings: true }, effectiveOptions: { trajectoryWarnings: false },
    error: 'DIAGNOSTIC ERROR', event: 'gate', reason: 'below-interval', steps: 11, interval: 15,
  } },
  { type: 'future-event', id: 'future', arbitrary: [null, false, 0, '', [], {}] },
];

test('default compact output preserves content and unknown types but not known message metadata', () => {
  const entries = fixture();
  entries.push(msg('custom-role', { role: 'future-role', content: 'FUTURE ROLE' }));
  const out = output(entries);
  for (const text of ['THOUGHT', 'PAYLOAD', 'DATA', 'FUTURE BLOCK', 'RESULT BODY', 'IMAGE DATA',
    'DIAGNOSTIC ERROR', 'configuredOptions', 'effectiveOptions', 'FUTURE ROLE', 'aborted', 'isError=false']) {
    assert.ok(out.includes(text), text);
  }
  for (const old of ['usage=', '未记录结果', '响应已中断', '显示 ', '省略前', '看某条完整内容']) {
    assert.ok(!out.includes(old), old);
  }
  assert.ok(rows(out).some(line => line.startsWith('[   3] --:--:-- [→ delegate]')));
  assert.equal(output(entries, ['--preset', 'all']), out);
  assert.match(output(entries, ['3']), /usage/);
});

test('all presets equal ordinary filters and default to 300-character row bodies', () => {
  const entries = fixture();
  const bundles = [
    ['all', []],
    ['conversation', ['--only', 'role:user,role:assistant', '--exclude', 'type:thinking,type:toolCall']],
    ['tools', ['--only', 'type:toolCall,role:toolResult']],
    ['trajectory', ['--only', 'role:user,role:assistant,type:toolCall,customType:capability-prefetch,customType:capability-trajectory-warning',
      '--exclude', 'type:thinking,role:toolResult,type:image']],
  ];
  for (const [name, args] of bundles) {
    assert.equal(output(entries, ['--preset', name]), output(entries, [...args, '--max-chars', '300']), name);
    assert.equal(output(entries, ['--preset', name, '--max-chars', '12']),
      output(entries, [...args, '--max-chars', '12']), name);
  }
  const conversation = output(entries, ['--preset', 'conversation']);
  for (const keep of ['REQUEST', 'RESPONSE', 'FUTURE BLOCK']) assert.ok(conversation.includes(keep));
  for (const omit of ['THOUGHT', 'PAYLOAD', 'RESULT BODY', 'IMAGE DATA']) assert.ok(!conversation.includes(omit));
});

test('explicit only replaces preset defaults; excludes append and remove message shells', () => {
  const entries = fixture();
  const out = output(entries, ['--preset', 'conversation', '--only', 'role:toolResult', '--exclude', 'image']);
  assert.ok(out.includes('RESULT BODY'));
  for (const omit of ['REQUEST', 'RESPONSE', 'IMAGE DATA']) assert.ok(!out.includes(omit));
  assert.equal(out, output(entries, ['--only', 'role:toolResult', '--exclude', 'thinking,toolCall,image']));
  assert.match(output(entries, ['--preset', 'conversation', '--only', 'toolCall']), /无可显示记录/);
  assert.ok(!rows(output(entries, ['--exclude', 'role:assistant'])).some(line => /^\[   3\]/.test(line)));
});

test('recursive selection keeps call arguments not unrelated siblings; repeat/comma options agree', () => {
  const entries = fixture();
  const out = output(entries, ['--only', 'type:toolCall']);
  for (const keep of ['[   3]', '[→ delegate]', 'task=work', 'content=PAYLOAD', 'model=model-name', 'DATA']) {
    assert.ok(out.includes(keep), keep);
  }
  for (const omit of ['THOUGHT', 'RESPONSE', 'FUTURE BLOCK', 'usage=', 'aborted']) assert.ok(!out.includes(omit), omit);
  assert.equal(output(entries, ['--only', 'role:user,role:assistant', '--exclude', 'thinking,image', '--exclude', 'toolCall']),
    output(entries, ['--only', 'role:user', '--only', 'role:assistant', '--exclude', 'thinking', '--exclude', 'image,toolCall']));
  assert.match(output(entries, ['--only', 'toolCall', '--exclude', 'role:assistant']), /无可显示记录/);
});

test('row-body limits are independent of navigation and raw detail', () => {
  const long = '中文'.repeat(500);
  const entries = [msg('long-record-id', { role: 'assistant', content: [call('new-tool', { task: long, content: long })] })];
  assert.ok(!output(entries).includes(long));
  assert.ok(output(entries, ['--max-chars', '0']).includes(long));
  const line = rows(output(entries, ['--only', 'toolCall', '--max-chars', '10']))[0];
  assert.match(line, /^\[   1\] --:--:-- \[→ new-tool\] .{10} …\(\+\d+c\)$/);
  assert.equal((line.match(/…\(/g) || []).length, 1);
  assert.deepEqual(JSON.parse(output(entries, ['1', '--json'])), sourceRows(entries)[0]);
  assert.deepEqual(JSON.parse(output(entries, ['long-record-id', '--json'])), sourceRows(entries)[0]);
});

test('prefetch splits readable recommendation and execution rows with one source number', () => {
  const host = { type: 'custom_message', id: 'host', customType: 'capability-prefetch',
    content: 'RECOMMENDATION', futureField: 'FUTURE HOST', details: {
      actions: [{ tool: 'bash', arguments: { command: 'SUGGESTION' } }],
      prefetch: { rounds: [{ messages: [
        { role: 'assistant', content: [{ type: 'text', text: 'HOST PROSE' }, call('bash', { command: 'actual-command' })] },
        { role: 'toolResult', toolCallId: 'call', toolName: 'bash', isError: false, content: 'HOST RESULT' },
      ] }] },
    } };
  const out = output([host], ['--max-chars', '0']);
  for (const keep of ['RECOMMENDATION', 'FUTURE HOST', 'SUGGESTION', 'HOST PROSE', 'actual-command', 'HOST RESULT']) {
    assert.ok(out.includes(keep), keep);
  }
  assert.ok(rows(out).length >= 3);
  assert.ok(rows(out).every(line => line.startsWith('[   1] ')));
  assert.match(out, /\[recommend → bash\] command=actual-command/);
  assert.match(out, /\[recommend ← bash\].*HOST RESULT/);
  const trajectory = output([host], ['--preset', 'trajectory', '--tail', '1', '--max-chars', '0']);
  assert.ok(trajectory.includes('RECOMMENDATION'));
  assert.ok(trajectory.includes('actual-command'));
  assert.ok(!trajectory.includes('HOST RESULT'));
  assert.deepEqual(JSON.parse(output([host], ['1', '--json'])), sourceRows([host])[0]);
  assert.ok(output([msg('wrapped', { ...host, role: 'custom' })], ['--preset', 'trajectory']).includes('RECOMMENDATION'));
});

test('diagnostics and new warnings are selected by exact customType', () => {
  const out = output(fixture(), ['--only', 'customType:capability-prefetch-diagnostic', '--max-chars', '0']);
  for (const text of ['[   5]', 'configuredOptions', 'effectiveOptions', 'true', 'false', 'DIAGNOSTIC ERROR']) {
    assert.ok(out.includes(text), text);
  }
  assert.ok(!out.includes('RESULT BODY'));
  assert.match(output(fixture(), ['--only', 'customType:capability-prefetch']), /无可显示记录/);
  const warning = { type: 'custom_message', customType: 'capability-trajectory-warning', id: 'warning', content: 'REVIEW WARNING' };
  assert.match(output([warning], ['--preset', 'trajectory']), /REVIEW WARNING/);
});

test('retired trace and conflicting/invalid options fail through launcher', () => {
  const trace = invoke(fixture(), ['--trace']);
  assert.equal(trace.status, 2);
  assert.match(trace.stderr, /--trace 已移除/);
  assert.match(trace.stderr, /--preset tools/);
  for (const args of [['--only', ''], ['--exclude', 'name:read'], ['--max-chars', '-1'],
    ['3', '--preset', 'tools'], ['3', '--json', '--max-chars', '10']]) {
    assert.equal(invoke(fixture(), args).status, 2, args.join(' '));
  }
});
