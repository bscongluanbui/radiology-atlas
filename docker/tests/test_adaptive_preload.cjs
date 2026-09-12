"use strict";
const assert=require('node:assert/strict'),vm=require('node:vm');
const {context,flush}=require('./test_unified_preload.cjs');
async function main(){
 const h=context();
 vm.runInContext('noteViewerInteraction();ensureFullSeriesPreload();',h.ctx);
 await flush(); assert.equal(h.calls.length,0,'full series pauses while interacting');
 assert.equal(h.timers.size,1);
 const [id,t]=[...h.timers][0];assert.equal(t.delay,180);h.timers.delete(id);t.fn();
 for(let i=0;i<150&&!h.ctx.window.viewerSliceCacheDiagnostics().seriesPreloadReady;i++)await flush();
 assert.equal(h.ctx.window.viewerSliceCacheDiagnostics().seriesPreloadCompleted,60,'full series resumes at idle');
 vm.runInContext('recordSeriesPreloadJob(seriesPreloadSession,2000,false);',h.ctx);
 assert.equal(vm.runInContext('seriesPreloadLimit(seriesPreloadSession)',h.ctx),1);
 vm.runInContext('recordSeriesPreloadJob(seriesPreloadSession,100,false);recordSeriesPreloadJob(seriesPreloadSession,100,false);',h.ctx);
 assert.equal(vm.runInContext('seriesPreloadLimit(seriesPreloadSession)',h.ctx),2);
 vm.runInContext('recordSeriesPreloadJob(seriesPreloadSession,100,true);',h.ctx);
 assert.equal(vm.runInContext('seriesPreloadLimit(seriesPreloadSession)',h.ctx),1);
 vm.runInContext('noteViewerInteraction();clearSliceCaches();',h.ctx);
 assert.equal(h.timers.size,0,'reset cancels interaction timer');
 console.log('ADAPTIVE_PRELOAD=PASS; interaction_pause=180ms; idle_series=60/60; slow_failure_limit=1; recovered_limit=2; reset_cancels');
}
main().catch(e=>{console.error(e);process.exitCode=1});
