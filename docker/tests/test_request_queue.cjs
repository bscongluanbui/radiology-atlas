"use strict";
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||path.join(__dirname,'../..'));
const ctx={window:{},AbortController};vm.runInNewContext(fs.readFileSync(root+'/offline_anatomy_viewer/request_queue.js','utf8'),ctx);
const Queue=ctx.window.ViewerRequestQueue, flush=()=>new Promise(r=>setImmediate(r));
async function main(){
 const q=new Queue({concurrency:2,background:1}), started=[],tasks=new Map();
 const factory=id=>signal=>{started.push(id);return new Promise((resolve,reject)=>{tasks.set(id,{resolve,reject,signal});signal.addEventListener('abort',()=>reject(Object.assign(new Error('cancel'),{name:'AbortError'})));});};
 const a=q.schedule('a',factory('a'),2), b=q.schedule('b',factory('b'),2), c=q.schedule('c',factory('c'),1);
 assert.deepEqual(started,['a']);const foreground=q.schedule('visible',factory('visible'),0);
 assert.deepEqual(started,['a','visible']);assert.equal(q.diagnostics().active,2);
 tasks.get('visible').resolve('frame');assert.equal(await foreground,'frame');await flush();
 assert.deepEqual(started,['a','visible'],'remaining slot stays reserved');
 const same=q.schedule('b',factory('must-not-run'),0);assert.equal(same,b);
 assert.deepEqual(started,['a','visible','b']);tasks.get('b').resolve('b');await b;
 tasks.get('a').resolve('a');await a;await flush();assert.equal(started.at(-1),'c');tasks.get('c').resolve('c');await c;await flush();
 const old=q.schedule('old',factory('old'),2), waiting=q.schedule('waiting',factory('waiting'),2);
 const rejectedOld=assert.rejects(old,{name:'AbortError'}),rejectedWaiting=assert.rejects(waiting,{name:'AbortError'});
 q.clear();await rejectedOld;await rejectedWaiting;await flush();assert(tasks.get('old').signal.aborted);assert(!started.includes('waiting'));
 assert.equal(q.diagnostics().active,0);assert.equal(q.jobs.size,0);
 const failed=q.schedule('retry',()=>Promise.reject(new Error('temporary')),0);await assert.rejects(failed);await flush();
 assert.equal(await q.schedule('retry',()=>42,0),42);await flush();assert.equal(q.diagnostics().active,0);
 console.log('REQUEST_QUEUE=PASS; foreground_reservation,near_priority,promotion,dedupe,cancel,stale_cleanup,retry,bounded_concurrency');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
