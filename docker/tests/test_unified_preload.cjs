"use strict";
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||path.join(__dirname,'../..'));
const app=fs.readFileSync(process.env.VIEWER_APP_SOURCE||root+'/offline_anatomy_viewer/app.js','utf8').replace(/\ninitialize\(\);\s*$/,'\n');
const flush=()=>new Promise(r=>setImmediate(r));
class Element{
 constructor(tag='img'){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.handlers={};this.isConnected=true;this.style={setProperty(){}};this.classList={add(){},remove(){},toggle(){}};}
 setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}
 addEventListener(k,v){this.handlers[k]=v;}remove(){this.isConnected=false;}dispatchEvent(e){this.handlers[e.type]?.(e);}
}
function context(memory=8,options={}){
 const calls=[],events={};let conversions=0;const sliceFailures=new Map(Object.entries(options.sliceFailures||{}).map(([n,count])=>[Number(n),Number(count)]));
 let timerId=0;const timers=new Map();
 class Reader{readAsDataURL(blob){conversions++;this.result='data:image/png;base64,'+Buffer.from(blob.key||'image').toString('base64');queueMicrotask(()=>this.onload());}}
 class Img extends Element{constructor(){super('img');this.naturalWidth=1890;this.naturalHeight=1091;}decode(){return Promise.resolve();}}
 const ctx=vm.createContext({window:{viewerRuntime:{remote:true,maxBytes:512*1024**2,ttlMs:1800000,decodedImages:32,decodeForward:20,decodeBackward:11,decodeConcurrency:2,preloadConcurrency:2,imageConcurrency:4},navigator:{deviceMemory:memory},addEventListener(n,fn){events[n]=fn;},dispatchEvent(e){events[e.type]?.(e);},setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);}},
   document:{createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)},Image:Img,FileReader:Reader,AbortController,URL,Event,
   location:{origin:'https://atlas.test',assign(){}},setInterval(){return 1},clearInterval(){},requestAnimationFrame:fn=>fn(),
   fetch:async(url,options={})=>{calls.push({url:String(url),priority:options.priority});const u=new URL(url,'https://atlas.test');
      if(u.pathname==='/api/slice'){const n=Number(u.searchParams.get('slice'));const remaining=sliceFailures.get(n)||0;if(remaining>0){sliceFailures.set(n,remaining-1);throw new Error('simulated network interruption');}return {ok:true,status:200,json:async()=>({image_url:`/data/BRAIN/mri-brain/rendered/31_Axial/default_Default/slice_${String(n).padStart(4,'0')}.png`})};}
      return {ok:true,status:200,blob:async()=>({size:100,key:String(url)})};},console});
 for(const name of ['anatomy_language.js','request_queue.js','resource_cache.js'])vm.runInContext(fs.readFileSync(root+'/offline_anatomy_viewer/'+name,'utf8'),ctx);
 ctx.AnatomyLanguage=ctx.window.AnatomyLanguage;
 vm.runInContext(app,ctx);
 vm.runInContext(`state.module={key:'BRAIN/mri-brain',series:[]};state.series={directory:'31_Axial'};state.variant={directory:'default_Default',slices:Array.from({length:60},(_,i)=>i+1)};state.slicePosition=10;state.dataRevision=53;state.seriesRevision=1;state.filmstripVisible=true;el.filmstrip=new Element('section');el.preloadStatus=new Element('span');`,Object.assign(ctx,{Element}));
 return {ctx,calls,events,timers,conversions:()=>conversions};
}
async function main(){
 const h=context(),{ctx}=h;
 // The native Series dropdown is deliberately flat: directly selectable child
 // variants carry their slice counts and no optgroup header consumes a row.
 vm.runInContext(`state.module.series=[
   {directory:'10_Sagittal',label:'Sagittal T1 T2 TSE',variants:[
     {directory:'1_T1',label:'T1',slice_count:15,slices:[1]},
     {directory:'2_T2',label:'T2',slice_count:15,slices:[1]}]},
   {directory:'20_Coronal',label:'Coronal T2 MPR',variants:[
     {directory:'default_Default',label:'Default',slice_count:116,slices:[1]}]},
   {directory:'empty',label:'Empty',variants:[{directory:'none',label:'None',slice_count:0,slices:[]}]}
 ]; el.toolbarWeightingSelect=new Element('select'); renderSeriesSelectors();`,ctx);
 assert.deepEqual(vm.runInContext('el.toolbarWeightingSelect.children.map(x=>x.tagName)',ctx),['option','option','option']);
 assert.deepEqual(vm.runInContext('el.toolbarWeightingSelect.children.map(x=>x.textContent)',ctx),[
   'Sagittal T1 T2 TSE - T1 (15 slices)','Sagittal T1 T2 TSE - T2 (15 slices)','Coronal T2 MPR - 116 slices']);
 assert.equal(vm.runInContext('el.toolbarWeightingSelect.children.some(x=>x.tagName==="optgroup")',ctx),false);
 assert.equal(vm.runInContext('el.toolbarWeightingSelect.disabled',ctx),false);
 const url=vm.runInContext('sliceImageUrl(11)',ctx);
 await vm.runInContext('warmSliceImageBytes(sliceImageUrl(11))',ctx);
 const before=h.calls.filter(c=>c.url===url).length;
 await vm.runInContext('decodeSliceImage(sliceImageUrl(11))',ctx);
 vm.runInContext("renderFilmstrip(); globalThisForTest={mpr:new Element(),overlay:new Element('image')};assignCachedImage(globalThisForTest.mpr,sliceImageUrl(11));assignCachedImage(globalThisForTest.overlay,sliceImageUrl(11),{svg:true,priority:1});",ctx);
 for(let i=0;i<15;i++)await flush();
 assert.equal(h.calls.filter(c=>c.url===url).length,before,'main,filmstrip,MPR and overlay share warm bytes');
 const targets=vm.runInContext('[globalThisForTest.mpr.src,globalThisForTest.overlay.attrs.href,el.filmstrip.children[3].children[0].src]',ctx);
 assert(targets.every(s=>s?.startsWith('data:')));assert.equal(new Set(targets).size,1);
 const requests=h.calls.length;vm.runInContext('renderFilmstrip()',ctx);for(let i=0;i<5;i++)await flush();assert.equal(h.calls.length,requests);
 vm.runInContext('state.filmstripVisible=false;renderFilmstrip()',ctx);assert.equal(vm.runInContext('el.filmstrip.children.length',ctx),0);
 // Opening a previously hidden filmstrip renders immediately, without scrolling.
 vm.runInContext(`savedVisibilityForTest=syncVisibilityControls;syncVisibilityControls=()=>{};
   savedPreferencesForTest=savePreferences;savePreferences=()=>{};window.setTimeout=()=>0;
   setVisibility('filmstripVisible',true);
   syncVisibilityControls=savedVisibilityForTest;savePreferences=savedPreferencesForTest;`,ctx);
 assert.equal(vm.runInContext('el.filmstrip.children.length',ctx),7);
 // Entire active series, and not other series, is still preloaded.
 vm.runInContext('ensureFullSeriesPreload()',ctx);
 for(let i=0;i<300&&!ctx.window.viewerSliceCacheDiagnostics().seriesPreloadReady;i++)await flush();
 const d=ctx.window.viewerSliceCacheDiagnostics();assert(d.seriesPreloadReady);assert.equal(d.seriesPreloadTotal,60);assert.equal(d.seriesPreloadCompleted,60);
 assert.equal(d.preloadConcurrency,2);assert.equal(d.metadataRequests.backgroundLimit,1);assert.equal(d.imageLimit,32);
 assert.equal(d.decodeForward,20);assert.equal(d.decodeBackward,11);assert.match(vm.runInContext('el.preloadStatus.textContent',ctx),/60\/60/);
 assert.equal(h.calls.filter(c=>c.url.includes('/api/slice?')).length,60);
 assert.equal(h.calls.filter(c=>c.url.includes('/rendered/')&&!c.url.includes('/api/')).length,60);
 const conversions=h.conversions();await vm.runInContext('decodeSliceImage(sliceImageUrl(11))',ctx);assert.equal(h.conversions(),conversions);
 // A transient interruption may exhaust the ordinary retry. Recovery resumes
 // only the failed slice, preserves completed counters, and does not duplicate
 // the already-warmed series when the online event follows.
 const recovery=context(8,{sliceFailures:{3:2}}),{ctx:recoveryCtx}=recovery;
 vm.runInContext(`state.module={key:'BRAIN/mri-brain',series:[]};state.series={directory:'31_Axial'};state.variant={directory:'default_Default',slices:[1,2,3,4,5,6]};state.slicePosition=0;state.dataRevision=53;state.seriesRevision=1;el.filmstrip=new Element('section');el.preloadStatus=new Element('span');bindSeriesPreloadRecoveryEvents();ensureFullSeriesPreload();`,recoveryCtx);
 for(let i=0;i<160&&recoveryCtx.window.viewerSliceCacheDiagnostics().seriesPreloadFailed!==1;i++)await flush();
 let interrupted=recoveryCtx.window.viewerSliceCacheDiagnostics();assert.equal(interrupted.seriesPreloadFailed,1);assert.equal(interrupted.seriesPreloadCompleted,5);
 recoveryCtx.window.dispatchEvent({type:'viewer-session-resumed'});
 for(let i=0;i<160&&!recoveryCtx.window.viewerSliceCacheDiagnostics().seriesPreloadReady;i++)await flush();
 let resumed=recoveryCtx.window.viewerSliceCacheDiagnostics();assert(resumed.seriesPreloadReady);assert.equal(resumed.seriesPreloadTotal,6);assert.equal(resumed.seriesPreloadCompleted,6);assert.equal(resumed.seriesPreloadFailed,0);
 assert.equal(recovery.calls.filter(c=>c.url.includes('/api/slice?')).length,8,'only the interrupted metadata retries');
 assert.equal(recovery.calls.filter(c=>c.url.includes('/rendered/')&&!c.url.includes('/api/')).length,6,'every image warms once');
 const resumedCalls=recovery.calls.length;recoveryCtx.window.dispatchEvent({type:'online'});for(let i=0;i<8;i++)await flush();assert.equal(recovery.calls.length,resumedCalls,'resume events do not duplicate successes');
 // Image/metadata failures can recover even when the session heartbeat stayed
 // healthy: the delayed sweep is bounded and retries the failed entry.
 const delayed=context(8,{sliceFailures:{2:2}}),{ctx:delayedCtx}=delayed;
 vm.runInContext(`state.module={key:'BRAIN/mri-brain',series:[]};state.series={directory:'31_Axial'};state.variant={directory:'default_Default',slices:[1,2,3,4]};state.slicePosition=0;state.dataRevision=53;state.seriesRevision=1;el.filmstrip=new Element('section');el.preloadStatus=new Element('span');bindSeriesPreloadRecoveryEvents();ensureFullSeriesPreload();`,delayedCtx);
 for(let i=0;i<160&&delayedCtx.window.viewerSliceCacheDiagnostics().seriesPreloadFailed!==1;i++)await flush();
 assert.equal(delayedCtx.window.viewerSliceCacheDiagnostics().seriesPreloadFailed,1);assert.equal(delayed.timers.size,1);
 const delayedTimer=[...delayed.timers.entries()][0];delayed.timers.delete(delayedTimer[0]);delayedTimer[1].fn();
 for(let i=0;i<160&&!delayedCtx.window.viewerSliceCacheDiagnostics().seriesPreloadReady;i++)await flush();
 const delayedResult=delayedCtx.window.viewerSliceCacheDiagnostics();assert(delayedResult.seriesPreloadReady);assert.equal(delayedResult.seriesPreloadCompleted,4);assert.equal(delayedResult.seriesPreloadFailed,0);
 // Permanently failing data consumes only three delayed sweeps, not an endless loop.
 const bounded=context(8,{sliceFailures:{1:999}});
 vm.runInContext(`state.variant.slices=[1];state.slicePosition=0;ensureFullSeriesPreload();`,bounded.ctx);
 for(let round=0;round<4;round++){
  for(let i=0;i<100;i++)await flush();
  assert.equal(bounded.ctx.window.viewerSliceCacheDiagnostics().seriesPreloadFailed,1);
  if(round<3){assert.equal(bounded.timers.size,1);const [id,t]=[...bounded.timers][0];bounded.timers.delete(id);t.fn();}
 }
 assert.equal(bounded.timers.size,0);assert.equal(bounded.calls.length,8,'two attempts per round, capped at four rounds');
 // A stale recovery timer is cancelled when the active series/revision changes.
 const stale=context(8,{sliceFailures:{2:99}}),{ctx:staleCtx}=stale;
 vm.runInContext(`state.module={key:'BRAIN/mri-brain',series:[]};state.series={directory:'31_Axial'};state.variant={directory:'default_Default',slices:[1,2,3]};state.slicePosition=0;state.dataRevision=53;state.seriesRevision=1;el.filmstrip=new Element('section');el.preloadStatus=new Element('span');bindSeriesPreloadRecoveryEvents();ensureFullSeriesPreload();`,staleCtx);
 for(let i=0;i<160&&staleCtx.window.viewerSliceCacheDiagnostics().seriesPreloadFailed!==1;i++)await flush();
 assert.equal(staleCtx.window.viewerSliceCacheDiagnostics().seriesPreloadFailed,1);assert.equal(stale.timers.size,1);
 vm.runInContext(`state.series={directory:'32_Coronal'};state.variant={directory:'other',slices:[11,12]};clearSliceCaches({advanceDataRevision:false});ensureFullSeriesPreload();`,staleCtx);
 for(let i=0;i<160&&!staleCtx.window.viewerSliceCacheDiagnostics().seriesPreloadReady;i++)await flush();
 const fresh=staleCtx.window.viewerSliceCacheDiagnostics();assert(fresh.seriesPreloadReady);assert.equal(fresh.seriesPreloadTotal,2);assert.equal(fresh.seriesPreloadCompleted,2);assert.equal(fresh.seriesPreloadFailed,0);assert.equal(stale.timers.size,0,'stale recovery timer cancelled on series reset');
 // Exercise the real renderers as well as their shared-source helper.
 vm.runInContext(`el.annotationLayer=new Element('svg');el.overlayCoverageStatus=new Element('span');
   state.activeFilters=new Set(['5']);state.capture={pixel_overlays:{status:'PASS',expected_count:4,valid_layers:[
     {status:'PASS',image_url:'/overlay/valid.png',filter_ids:['5'],transform:[1,0,0,1,12,24],width:100,height:90,layer:5},
     {status:'FAIL',image_url:'/overlay/invalid.png',filter_ids:['5'],transform:[1,0,0,1,0,0]},
     {status:'PASS',image_url:'/overlay/inactive.png',filter_ids:['6'],transform:[1,0,0,1,0,0]},
     {status:'PASS',image_url:'/overlay/bad-transform.png',filter_ids:['5'],transform:[1,0,0,1,NaN,0]}
   ]}};renderPixelOverlays();`,ctx);
 for(let i=0;i<5;i++)await flush();
 assert.equal(vm.runInContext('el.annotationLayer.children.length',ctx),1);
 assert.equal(vm.runInContext('el.annotationLayer.children[0].attrs.transform',ctx),'matrix(1 0 0 1 12 24)');
 assert.match(vm.runInContext('el.annotationLayer.children[0].attrs.href',ctx),/^data:/);
 assert.equal(h.calls.filter(c=>c.url.includes('/overlay/')).length,1,'invalid/inactive overlays never fetched');
 const overlayRequests=h.calls.length;vm.runInContext('el.annotationLayer.replaceChildren();renderPixelOverlays()',ctx);
 for(let i=0;i<5;i++)await flush();assert.equal(h.calls.length,overlayRequests);
 vm.runInContext(`state.module.series=[{directory:'32_Coronal',label:'Coronal',sort_order:32,variants:[{directory:'default_Default',label:'Default',slice_count:1,slices:[1]}]}];
   state.mprVisible=true;el.mprViews=new Element('aside');renderMprPanel();`,ctx);
 for(let i=0;i<5;i++)await flush();
 assert.match(vm.runInContext('el.mprViews.children[0].children[1].children[0].src',ctx),/^data:/);
 const mprRequests=h.calls.length;vm.runInContext('renderMprPanel()',ctx);
 for(let i=0;i<5;i++)await flush();assert.equal(h.calls.length,mprRequests,'MPR repaint reuses cache');
 // A detached/recycled element cannot receive a late image from another slice.
 const source=ctx.window.viewerResourceCache.source;let resolve;
 ctx.window.viewerResourceCache.source=()=>new Promise(r=>resolve=r);
 vm.runInContext("staleImage=new Element();assignCachedImage(staleImage,'/old');staleImage.isConnected=false;",ctx);
 resolve('data:old');await flush();assert.equal(vm.runInContext('staleImage.src',ctx),undefined);
 ctx.window.viewerResourceCache.source=source;
 vm.runInContext('clearSliceCaches()',ctx);assert.equal(ctx.window.viewerSliceCacheDiagnostics().captures,0);assert.equal(ctx.window.viewerSliceCacheDiagnostics().encoded.bytes,0);
 // Low-memory clients use a balanced 10-forward/5-back window and 16 decoded frames.
 const low=context(4).ctx.window.viewerSliceCacheDiagnostics();assert.equal(low.imageLimit,16);assert.equal(low.decodeForward,10);assert.equal(low.decodeBackward,5);assert.equal(low.encoded.maxBytes,256*1024**2);
 const markup=fs.readFileSync(root+'/offline_anatomy_viewer/index.html','utf8');assert(markup.indexOf('./request_queue.js')<markup.indexOf('./resource_cache.js'));
 console.log('UNIFIED_PRELOAD=PASS; series_dropdown=flat_child_options_with_slice_counts; series=60/60; JSON_requests=60; image_requests=60; warm_repeat_requests=0; main_filmstrip_MPR_overlay=shared; stale_detached=blocked; low_memory=16_frames; preload_status=PASS');
}
module.exports={context,Element,flush};
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
