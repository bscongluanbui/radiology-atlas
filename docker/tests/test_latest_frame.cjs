"use strict";
const assert=require('node:assert/strict'),vm=require('node:vm');
const {context,Element,flush}=require('./test_unified_preload.cjs');
async function main(){
 const {ctx}=context();
 vm.runInContext(`
 el.emptyState=new Element();el.errorState=new Element();el.loadingState=new Element();el.app=new Element();
 hideTooltip=()=>{};showError=(message)=>{throw Error(message)};
 const waits=new Map();const frames=[];let jobs=0,maxJobs=0;
 applySliceDescriptor=async(descriptor)=>{
  jobs++;maxJobs=Math.max(maxJobs,jobs);
  await new Promise(resolve=>waits.set(descriptor.number,resolve));jobs--;
  if(!descriptorIsCurrent(descriptor))return false;
  frames.push(descriptor.number);return true;
 };
 state.slicePosition=0;showCurrentSlice();`,ctx);
 await flush();
 vm.runInContext('state.slicePosition=1;showCurrentSlice();',ctx);await flush();
 assert.equal(vm.runInContext('waits.has(2)',ctx),true,'new frame starts while old request is slow');
 vm.runInContext('waits.get(2)();',ctx);await flush();
 assert.deepEqual(Array.from(vm.runInContext('frames',ctx)),[2]);
 vm.runInContext('state.slicePosition=2;showCurrentSlice();',ctx);await flush();
 vm.runInContext('waits.get(3)();',ctx);await flush();
 vm.runInContext('waits.get(1)();',ctx);await flush();
 assert.deepEqual(Array.from(vm.runInContext('frames',ctx)),[2,3],'obsolete frame never commits');
 assert.equal(vm.runInContext('maxJobs',ctx),2,'work bounded across worker restarts');
 const layout=context().ctx;
 vm.runInContext(`
 el.annotationLayer=new Element();el.annotationLayer.toggleAttribute=()=>{};
 updateAnatomyNameStatus=()=>{};renderPixelOverlays=()=>{};
 labelFilterEnabled=(label)=>label.visible!==false;
 overlaySelectionKey=()=>null;geometryLabelGroups=()=>new Map();
 const layouts=[];
 renderVisibleLabel=(label,key,labels,sizes)=>{layouts.push(sizes);};
 state.capture={labels:[{x:1,y:2}],geometry:[]};
 renderOverlay();renderOverlay();
 `,layout);
 assert.equal(vm.runInContext('layouts[0]===layouts[1]',layout),true);
 vm.runInContext('state.capture={labels:[{x:1,y:2}],geometry:[]};renderOverlay();',layout);
 assert.equal(vm.runInContext('layouts[1]===layouts[2]',layout),false,'new capture never reuses old anatomy layout');
 console.log('LATEST_FRAME=PASS; newest_starts_before_slow_old; stale_never_commits; max_jobs=2');
}
main().catch(e=>{console.error(e);process.exitCode=1});
