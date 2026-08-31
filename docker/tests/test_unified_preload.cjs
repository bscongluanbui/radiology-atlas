"use strict";
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||path.join(__dirname,'../..'));
const app=fs.readFileSync(root+'/offline_anatomy_viewer/app.js','utf8').replace(/\ninitialize\(\);\s*$/,'\n');
const flush=()=>new Promise(r=>setImmediate(r));
class Element{
 constructor(tag='img'){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.handlers={};this.isConnected=true;this.style={setProperty(){}};this.classList={add(){},remove(){},toggle(){}};}
 setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}
 addEventListener(k,v){this.handlers[k]=v;}remove(){this.isConnected=false;}dispatchEvent(e){this.handlers[e.type]?.(e);}
}
function context(memory=8){
 const calls=[],events={};let conversions=0;
 class Reader{readAsDataURL(blob){conversions++;this.result='data:image/png;base64,'+Buffer.from(blob.key||'image').toString('base64');queueMicrotask(()=>this.onload());}}
 class Img extends Element{constructor(){super('img');this.naturalWidth=1890;this.naturalHeight=1091;}decode(){return Promise.resolve();}}
 const ctx=vm.createContext({window:{viewerRuntime:{remote:true,maxBytes:512*1024**2,ttlMs:1800000,decodedImages:32,decodeForward:20,decodeBackward:11,decodeConcurrency:2,preloadConcurrency:2,imageConcurrency:4},navigator:{deviceMemory:memory},addEventListener(n,fn){events[n]=fn}},
   document:{createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)},Image:Img,FileReader:Reader,AbortController,URL,Event,
   location:{origin:'https://atlas.test',assign(){}},setInterval(){return 1},clearInterval(){},requestAnimationFrame:fn=>fn(),
   fetch:async(url,options={})=>{calls.push({url:String(url),priority:options.priority});const u=new URL(url,'https://atlas.test');
      if(u.pathname==='/api/slice'){const n=Number(u.searchParams.get('slice'));return {ok:true,status:200,json:async()=>({image_url:`/data/BRAIN/mri-brain/rendered/31_Axial/default_Default/slice_${String(n).padStart(4,'0')}.png`})};}
      return {ok:true,status:200,blob:async()=>({size:100,key:String(url)})};},console});
 for(const name of ['anatomy_language.js','request_queue.js','resource_cache.js'])vm.runInContext(fs.readFileSync(root+'/offline_anatomy_viewer/'+name,'utf8'),ctx);
 ctx.AnatomyLanguage=ctx.window.AnatomyLanguage;
 vm.runInContext(app,ctx);
 vm.runInContext(`state.module={key:'BRAIN/mri-brain',series:[]};state.series={directory:'31_Axial'};state.variant={directory:'default_Default',slices:Array.from({length:60},(_,i)=>i+1)};state.slicePosition=10;state.dataRevision=53;state.seriesRevision=1;state.filmstripVisible=true;el.filmstrip=new Element('section');el.preloadStatus=new Element('span');`,Object.assign(ctx,{Element}));
 return {ctx,calls,events,conversions:()=>conversions};
}
async function main(){
 const h=context(),{ctx}=h;
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
 console.log('UNIFIED_PRELOAD=PASS; series=60/60; JSON_requests=60; image_requests=60; warm_repeat_requests=0; main_filmstrip_MPR_overlay=shared; stale_detached=blocked; low_memory=16_frames; preload_status=PASS');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
