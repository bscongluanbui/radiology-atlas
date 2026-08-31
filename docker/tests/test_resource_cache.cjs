const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=process.argv[2],source=fs.readFileSync(root+'/offline_anatomy_viewer/resource_cache.js','utf8');
async function main(){
 const offline={window:{}};vm.runInNewContext(source,offline);assert.equal(offline.window.viewerResourceCache,null);
 let now=100000,fetches=0,tick;const pending=[];
 const ctx={window:{viewerRuntime:{remote:true,maxBytes:16*1024**2,ttlMs:60000},addEventListener(){}},Date:{now:()=>now},Map,Number,Math,Promise,Error,
 setInterval(fn){tick=fn;return 1},clearInterval(){},location:{assign(){}},fetch:()=>{fetches++;return new Promise(resolve=>pending.push(resolve));}};
 vm.runInNewContext(source,ctx);const cache=ctx.window.viewerResourceCache;
 const serve=bytes=>pending.shift()({ok:true,status:200,blob:async()=>({size:bytes})});
 const first=cache.load('one');const duplicate=cache.load('one');assert.equal(fetches,1);serve(10*1024**2);await first;await duplicate;
 await cache.load('one');assert.equal(fetches,1);
 const second=cache.load('two');serve(10*1024**2);await second;assert.equal(cache.diagnostics().entries,1);assert(cache.diagnostics().bytes<=16*1024**2);
 now+=61000;tick();assert.equal(cache.diagnostics().bytes,0);
 const old=cache.load('old');cache.clear();const current=cache.load('new');serve(100);await old;assert.equal(cache.diagnostics().entries,0);serve(100);await current;assert.equal(cache.diagnostics().entries,1);
 const huge=cache.load('huge');serve(20*1024**2);await huge;assert.equal(cache.diagnostics().entries,1);
 const bad=cache.load('bad');pending.shift()({ok:false,status:503,statusText:'retry'});await assert.rejects(bad);const retry=cache.load('bad');serve(20);await retry;
 cache.clear();assert.equal(cache.diagnostics().bytes,0);
 console.log('RESOURCE_CACHE=PASS; offline=unchanged; dedupe=PASS; byte_budget=PASS; ttl=PASS; clear_race=PASS; retry=PASS');
}main().catch(error=>{console.error(error);process.exit(1)});
