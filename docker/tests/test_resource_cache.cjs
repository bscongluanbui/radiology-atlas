"use strict";
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||path.join(__dirname,'../..'));
const source=fs.readFileSync(root+'/offline_anatomy_viewer/resource_cache.js','utf8'),queueSource=fs.readFileSync(root+'/offline_anatomy_viewer/request_queue.js','utf8');
const flush=()=>new Promise(r=>setImmediate(r));
function setup(memory=8){
 let now=100000,fetches=0,conversions=0,starts=0,redirects=0;const pending=[],events={};let tick;
 class Reader{readAsDataURL(blob){conversions++;this.result='data:image/png;base64,'+(blob.content||'AAAA');queueMicrotask(()=>this.onload());}}
 const ctx={window:{viewerRuntime:{remote:true,maxBytes:16*1024**2,ttlMs:60000},navigator:{deviceMemory:memory},addEventListener(n,fn){events[n]=fn}},
   AbortController,FileReader:Reader,Date:{now:()=>now},location:{assign(){redirects++;}},
   setInterval(fn){tick=fn;starts++;return starts},clearInterval(){},
   fetch(url,options){fetches++;return new Promise((resolve,reject)=>{const item={url,resolve,reject,aborted:false};pending.push(item);options.signal.addEventListener('abort',()=>{item.aborted=true;reject(Object.assign(new Error('cancelled'),{name:'AbortError'}));});})}};
 vm.runInNewContext(queueSource,ctx);vm.runInNewContext(source,ctx);
 const serve=(size,status=200)=>{while(pending[0]?.aborted)pending.shift();const item=pending.shift();assert(item,'pending request');item.resolve({ok:status===200,status,statusText:'test',blob:async()=>({size})});};
 return {cache:ctx.window.viewerResourceCache,ctx,serve,events,pending,advance(ms){now+=ms;tick()},stats:()=>({fetches,conversions,starts,redirects})};
}
async function main(){
 const offline={window:{}};vm.runInNewContext(source,offline);assert.equal(offline.window.viewerResourceCache,null);
 const h=setup(),c=h.cache;
 const a=c.load('one'),b=c.load('one');assert.equal(h.stats().fetches,1);h.serve(10*1024**2);await Promise.all([a,b]);await flush();
 await c.load('one');assert.equal(h.stats().fetches,1);
 const urls=await Promise.all([c.source('one'),c.source('one'),c.source('one')]);assert.equal(new Set(urls).size,1);assert.equal(h.stats().conversions,1);
 assert(c.diagnostics().bytes>10*1024**2,'data URL is charged to budget');
 const second=c.load('two');h.serve(10*1024**2);await second;await flush();assert.equal(c.diagnostics().entries,1);assert(c.diagnostics().bytes<=16*1024**2);
 h.advance(61000);assert.equal(c.diagnostics().bytes,0);
 const old=c.load('old');const rejectOld=assert.rejects(old,{name:'AbortError'});c.clear();await rejectOld;await flush();
 const next=c.load('new');h.serve(100);await next;await flush();assert.equal(c.diagnostics().entries,1);
 const huge=c.load('huge');h.serve(20*1024**2);await huge;await flush();assert(!c.has('huge'));assert(c.has('new'));
 const bad=c.load('bad');h.serve(1,503);await assert.rejects(bad);await flush();const retry=c.load('bad');h.serve(20);await retry;await flush();
 h.events.pagehide();assert.equal(c.diagnostics().bytes,0);h.events.pageshow();assert.equal(h.stats().starts,2);
 const forbidden=c.load('private');const deny=assert.rejects(forbidden);h.serve(1,401);await deny;await flush();assert.equal(h.stats().redirects,1);assert.equal(c.diagnostics().bytes,0);
 // Device cap is a browser-memory limit, independent of VPS RAM.
 for(const [memory,cap] of [[2,128],[4,256],[8,512]]){
   const t=setup(memory);t.ctx.window.viewerRuntime.maxBytes=512*1024**2;vm.runInNewContext(source,t.ctx);
   assert.equal(t.ctx.window.viewerResourceCache.diagnostics().maxBytes,cap*1024**2);
 }
 console.log('RESOURCE_CACHE=PASS; shared_dedupe,memoized_data_URL,byte_budget,TTL,abort_clear,retry,401,pageshow_timer,device_caps');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
