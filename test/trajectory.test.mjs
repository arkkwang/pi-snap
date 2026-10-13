import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
const msg = (id, message) => ({type:'message', id, message});
const call = (name, args, id='call') => ({type:'toolCall', id, name, arguments:args});
const output = (entries, args=[]) => {
 const dir=mkdtempSync(path.join(tmpdir(),'pi snap trajectory '));
 try {
  const file=path.join(dir,'session.jsonl');
  writeFileSync(file,entries.map((e,i)=>JSON.stringify({parentId:i?entries[i-1].id:null,...e})).join('\n'));
  const r=spawnSync(process.execPath,[cli,file,...args],{encoding:'utf8'});
  assert.equal(r.status,0,r.stderr);return r.stdout;
 } finally {rmSync(dir,{recursive:true,force:true});}
};
test('default shares calls/status/events without payloads; dialogue remains complete', () => {
 const long='任务中文\n'+'完整正文'.repeat(1000)+'\nEND';
 const entries = [msg('u', {role:'user',content:long}),
 msg('a',{role:'assistant', stopReason:'aborted',content:[{type:'thinking',thinking:'THINK_SECRET'}, {type:'text',text:long},call('read',{path:'/a',offset:3,limit:5})]}),
 msg('r',{role:'toolResult',toolCallId:'call',toolName:'read',isError:true,content:[{type:'text',text:'OUTPUT_SECRET'},{type:'image',data:'IMAGE_SECRET'}]}),
 {type:'compaction',id:'c',summary:'SUMMARY_SECRET'},
 {type:'context_edit',id:'e',targetId:'r',replacement:null},
 {type:'branch_summary',id:'b',fromId:'old',summary:'BRANCH_SECRET'}];
 const s=output(entries); for(const text of [long.replaceAll('\n','\r\n'),'/a @3+5','响应已中断','ERR','compact','branch_summary']) assert.ok(s.replaceAll('\r\n','\n').includes(text.replaceAll('\r\n','\n')),text);
 assert.ok(!s.includes('context_edit')); // 上下文摘除属宿主处理痕迹，轨迹不透出
 for(const text of ['THINK_SECRET','OUTPUT_SECRET','IMAGE_SECRET','SUMMARY_SECRET','BRANCH_SECRET','未记录结果']) assert.ok(!s.includes(text),s);
 const t=output(entries,['--trace']); assert.ok(t.includes('context_edit'));
 assert.ok(t.indexOf('[c]')<t.indexOf('[e]') && t.indexOf('[e]')<t.indexOf('[b]'));
 assert.match(output(entries,['e']),/context_edit/); // 单条展开仍可查
 assert.match(output(entries,['r']),/OUTPUT_SECRET/);
});
test('trajectory caps tool arguments at 100 chars; payloads stay excluded',()=> {
 const long='x'.repeat(500);
 const known=output([msg('a',{role:'assistant',content:[call('bash',{command:long})]})]);
 const unknown=output([msg('a',{role:'assistant',content:[call('delegate',{task:long,content:'SECRET'})]})]);
 for(const s of [known,unknown]){
  const line=s.split('\n').find(l=>l.includes('[→ '));
  assert.ok(line && !line.includes('x'.repeat(150)) && line.includes('…(+'), line);
 }
 assert.ok(!unknown.includes('SECRET')); // payload 字段仍被排除，不因截断而透出
});
test('bounded unknown arguments preserve model/cwd/task; payloads excluded',()=> {
 const s=output([msg('a',{role:'assistant',content:[call('delegate',{content:'PAYLOAD_SECRET',base64:'BASE64_SECRET',model:'m',cwd:'/cwd',task:'work',nested:{data:'DATA_SECRET'},huge:'x'.repeat(10000)})]})]);
 for(const text of ['model','cwd','task','未记录结果']) assert.ok(s.includes(text),s);
 for(const text of ['PAYLOAD_SECRET','BASE64_SECRET','DATA_SECRET']) assert.ok(!s.includes(text),s);
 assert.ok(s.length<1500);
});
test('only actual embedded host calls/results, never suggestions',()=> {
 const host={type:'custom_message',id:'host',customType:'capability-prefetch',content:'SUGGEST_SECRET',details:{actions:[{tool:'bash',arguments:{command:'SUGGEST_SECRET'}}],prefetch:{rounds:[{messages:[{role:'assistant',content:[{type:'text',text:'HOST_PROSE_SECRET'},call('bash',{command:'actual-command'})]},{role:'toolResult',toolCallId:'call',toolName:'bash',isError:false,content:'RESULT_SECRET'}]}]}}};
 const s=output([host]); assert.match(s,/host.*→ bash.*actual-command/); assert.ok(!s.includes('[← bash]')); // 成功结果不透出
 assert.ok(!s.includes('SECRET')); assert.ok(!s.includes('未记录结果')); assert.ok(!s.includes('~[host'));
 assert.match(s,/\[host\] \[recommend\] \[→ bash\]/); assert.ok(!/\[recommend \d/.test(s));
 assert.match(output([host],['host']),/SUGGEST_SECRET/); // Printed source ID resolves to the original record.
 assert.match(output([host],['--tail','1']),/→ bash/);
 const customRole=msg('custom',{...host,role:'custom'}); assert.match(output([customRole]),/actual-command/);
});
test('file chooses parent branch; ranges pair against the whole branch',()=> {
 const a=msg('a',{role:'assistant',content:[call('bash',{command:'check'})]});
 const r={...msg('r',{role:'toolResult',toolCallId:'call',toolName:'bash',isError:false,content:'RESULT_SECRET'}),parentId:'a'};
 const u={...msg('u',{role:'user',content:'CURRENT'}),parentId:'a'};
 const s=output([a,r,u]);assert.match(s,/CURRENT/);assert.match(s,/未记录结果/);assert.ok(!s.includes('[← bash]'));
 assert.ok(!output([a,r,u],['--all']).includes('[← bash]')); // 成功结果在 --all 下同样不透出
 const ranged=output([a,r],['--before','r']);assert.match(ranged,/check/);assert.ok(!ranged.includes('未记录结果'));
 const trace=output([a,r],['--trace']);assert.match(trace,/RESULT_SECRET/);
});

const delayedFixture = () => {
 const stamp = n => `2026-10-13T00:00:0${n}.000Z`;
 const u = {...msg('user', {role:'user',content:'REQUEST'}),timestamp:stamp(0)};
 const a = {...msg('main', {role:'assistant',content:'MAIN RESPONSE'}),timestamp:stamp(3)};
 const host = {type:'custom_message',id:'host',timestamp:stamp(3),customType:'capability-prefetch',details:{prefetch:{rounds:[{messages:[
  {role:'assistant',timestamp:Date.parse(stamp(1)),content:[call('read',{path:'skill.md'})]},
  {role:'toolResult',timestamp:Date.parse(stamp(2)),toolCallId:'call',toolName:'read',isError:false,content:'BODY_SECRET'},
 ]}]}}};
 return [u,a,host];
};
test('delayed preparation is displayed before main response with actual time and persistence provenance',()=> {
 const fixture=delayedFixture();
 for(const args of [[],['--trace']]) {
  const s=output(fixture,args);
  assert.ok(s.indexOf('[host]') < s.indexOf('[main]'),s);
  assert.match(s,/\[host\] \[\d\d:00:01\] \[recommend\] \[→ read\]/);
  assert.match(s,/落盘.*:00:03/);
  assert.match(s,/执行时间/);
 }
 assert.ok(!output(fixture).includes('BODY_SECRET'));
 assert.ok(!output(fixture,['--tail','1']).includes('[host]'));
 assert.match(output(fixture,['host','--json']),/"timestamp": 1791849601000/);
 // Range membership still follows original entry chain, not projected time.
 assert.ok(!output(fixture,['--before','main']).includes('[host]'));
 assert.ok(!output(fixture,['--since','host']).includes('[main]'));
 assert.ok(output(fixture,['--all']).indexOf('[main]')<output(fixture,['--all']).indexOf('[host]'));
});
test('unknown, contradictory timestamps and structural boundaries never invent an execution order',()=> {
 for (const value of [undefined,'bad',Date.parse('2026-10-13T00:00:09Z')]) {
  const f=delayedFixture();f[2].details.prefetch.rounds[0].messages[0].timestamp=value;
  const s=output(f);assert.ok(s.indexOf('[main]')<s.indexOf('[host]'),s);
  if(value===undefined||value==='bad') assert.match(s,/执行时间未知/);
 }
 for(const boundary of [{type:'compaction',id:'boundary',summary:'hidden'},
    msg('boundary',{role:'user',content:'NEXT INPUT'})]) {
  const f=delayedFixture();f.splice(2,0,boundary);
  const s=output(f);assert.ok(s.indexOf('[main]')<s.indexOf('[host]'),s);
 }
});
test('diagnostics are discoverable only in trace; configured and effective switches are not conflated',()=> {
 const d={type:'custom',id:'diag',customType:'capability-prefetch-diagnostic',data:{event:'start',trigger:'user-input',
  configuredOptions:{trajectoryWarnings:true},effectiveOptions:{trajectoryWarnings:false},error:'SECRET_ERROR'}};
 const gate={type:'custom',id:'gate',customType:'capability-prefetch-diagnostic',data:{event:'gate',reason:'below-interval',steps:11,interval:15,enabled:true}};
 assert.ok(!output([d,gate]).includes('[diag]'));
 const s=output([d,gate],['--trace']);
 assert.match(s,/configuredReview=true/);assert.match(s,/effectiveReview=false/);
 assert.match(s,/below-interval/);assert.match(s,/steps=11\/15/);assert.ok(!s.includes('SECRET_ERROR'));
 assert.match(output([d],['diag','--json']),/SECRET_ERROR/);
});
