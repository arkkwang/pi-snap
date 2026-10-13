// Read-only benchmark: node test/trajectory-benchmark.mjs
// Uses the installed command for the Bash/shim rows; never installs or changes PATH.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const cli=fileURLToPath(new URL('../cli.mjs',import.meta.url));
const snap=fileURLToPath(new URL('../snap.py',import.meta.url));
const python=process.env.PI_SNAP_PYTHON || 'python3';
const entries=[];
for(let i=0;i<100;i++) {
 entries.push({type:'message',message:{role:'user',content:`任务 ${i} `+'request '.repeat(40)}});
 entries.push({type:'message',message:{role:'assistant',content:[{type:'text',text:'checking '.repeat(40)},{type:'thinking',thinking:'private '.repeat(250)},{type:'toolCall',id:`c${i}`,name:'bash',arguments:{command:`check --step ${i}`}}]}});
 entries.push({type:'message',message:{role:'toolResult',toolCallId:`c${i}`,toolName:'bash',isError:false,content:[{type:'text',text:'output '.repeat(1500)}]}});
}
const input=entries.map((e,i)=>JSON.stringify({...e,id:`e${i}`,parentId:i?`e${i-1}`:null})).join('\n');
const dir=mkdtempSync(path.join(tmpdir(),'pi-snap-bench '));
try {
 const file=path.join(dir,'session.jsonl');writeFileSync(file,input);const args=[file,'--tail','160'];
 console.log(JSON.stringify({platform:process.platform,node:process.version,records:entries.length,inputBytes:Buffer.byteLength(input),samples:15}));
 for(const [label,command,argv] of [
  ['python-start',python,['-c','pass']],
  ['python-help',python,[snap,'--help']],
  ['node-help',process.execPath,[cli,'--help']],
  ['bash-shim-help','bash',['-c','exec pi-snap "$@"','pi-snap','--help']],
  ['python-file',python,[snap,...args]],
  ['node-file',process.execPath,[cli,...args]],
  ['bash-shim-file','bash',['-c','exec pi-snap "$@"','pi-snap',...args]],
 ]) {
  const times=[];let chars;
  for(let i=0;i<16;i++) {const start=performance.now();const r=spawnSync(command,argv,{encoding:'utf8',timeout:10000,maxBuffer:8*1024*1024});const ms=performance.now()-start;assert.equal(r.status,0,r.stderr || r.error?.message);chars=r.stdout.length;if(i)times.push(ms);}
  times.sort((a,b)=>a-b);console.log(JSON.stringify({label,medianMs:+times[7].toFixed(1),p95Ms:+times[14].toFixed(1),outputChars:chars}));
 }
} finally {rmSync(dir,{recursive:true,force:true});}
