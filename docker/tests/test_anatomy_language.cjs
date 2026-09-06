"use strict";
// Presentation-only fixtures: these strings are not anatomical translations.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const root = path.resolve(process.argv[2] || path.join(__dirname, '../..'));
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.attrs={}; this.dataset={}; this.style={}; this.handlers={}; this.classes=new Set();
    this.classList={toggle:(k,on)=>on?this.classes.add(k):this.classes.delete(k),add:(...keys)=>keys.forEach(k=>this.classes.add(k))}; }
  set textContent(value) { this.text=String(value); this.children=[]; } get textContent() { return this.children.length?this.children.map(n=>n.textContent).join(''):this.text||''; }
  append(...nodes) { nodes.forEach(n=>n.parentNode=this); this.children.push(...nodes); }
  replaceChildren(...nodes) { this.text=''; this.children=[]; this.append(...nodes); }
  setAttribute(k,v) { this.attrs[k]=String(v); } addEventListener(k,v) { this.handlers[k]=v; }
}
const ctx=vm.createContext({window:{addEventListener(){}},document:{createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)},Element,console,URL,AbortController,setInterval(){},clearInterval(){}});
vm.runInContext(fs.readFileSync(path.join(root,'offline_anatomy_viewer/anatomy_language.js'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(root,'offline_anatomy_viewer/request_queue.js'),'utf8'),ctx);
ctx.AnatomyLanguage=ctx.window.AnatomyLanguage;
vm.runInContext(fs.readFileSync(path.join(root,'offline_anatomy_viewer/app.js'),'utf8').replace(/\ninitialize\(\);\s*$/,'\n'),ctx);
const run=code=>vm.runInContext(code,ctx), A=ctx.AnatomyLanguage;
let tests=0; function test(name,fn) { fn(); tests++; console.log('PASS '+name); }
test('reviewed + exact source; stale/draft/malformed fields fall back',()=>{
  const p={structures:{'1:7':{status:'reviewed',source:{name:'Source'},translation:{name:'Mẫu thử'}}}};
  assert.equal(A.field(p,'structures','1:7','name','Source'),'Mẫu thử');
  assert.equal(A.field(p,'structures','1:7','name','Changed'),'Changed');
  assert.equal(A.field(p,'structures','2:7','name','Source'),'Source');
  for(const row of [null,[],42,{status:'draft'},{status:'reviewed',source:{name:'Source'},translation:{name:''}}])
    assert.equal(A.field({structures:{k:row}},'structures','k','name','Source'),'Source');
});
test('incremental field review keeps approved name but hides stale description',()=>{
  const p={structures:{x:{status:'reviewed',source:{name:'Source',description_text:'New'},
    translation:{name:'Mẫu',description_text:'Old translation'},field_status:{name:'reviewed',description_text:'needs_review'}}}};
  assert.equal(A.field(p,'structures','x','name','Source'),'Mẫu');
  assert.equal(A.field(p,'structures','x','description_text','New'),'New');
  const value=A.resolve(p,'structures','x','description_text','New');
  assert.equal(A.lines('vi','New',value)[0].text,'New');
  assert.equal(A.lines('en-vi','New',value)[1].missing,true);
  for(const status of [{name:'reviewed'},null,{},'reviewed']){
    p.structures.x.field_status=status;assert.equal(A.field(p,'structures','x','description_text','New'),'New');
  }
});
test('two distinct language rows, missing and same-spelling reviewed values',()=>{
  assert.equal(A.locale('en-vi'),'vi'); assert.equal(A.locale('ja'),'ja');
  const lines=A.lines('en-vi','Source',{text:'Mẫu thử',translated:true});
  assert.equal(lines.length,2); assert.equal(lines[0].lang,'en'); assert.equal(lines[1].lang,'vi');
  assert.equal(lines[1].text,'Mẫu thử'); assert.equal(A.lines('en-vi','Source',{text:'Source',translated:false})[1].missing,true);
  assert.equal(A.lines('en-vi','X',{text:'X',translated:true})[1].missing,false);
  assert.equal(A.lines('vi','Source',{text:'Source',translated:false})[0].lang,'en');
});
test('Unicode search, Vietnamese accentless, brackets and non-Latin alphabets',()=>{
  assert.equal(A.searchText('Đường [Mẫu] thử'),'duong mau thu');
  assert.equal(A.searchText('日本語 αβγ Кость'),'日本語 αβγ кость');
});
test('wrapped adjacent labels fit two rows without moving source anchors',()=>{
  const a={x:100,y:100,text_align:'left'},b={x:100,y:124,text_align:'left'};
  assert.equal(A.labelFontSize(a,[a,b]),8);assert.equal(A.labelFontSize(b,[a,b]),8);
  assert.equal(A.labelFontSize(a,[a,{...b,x:1000}]),16);
  assert.equal(A.labelKey('S','V',5,null,0),'["S","V","5","",0]');
  assert.equal(A.targetKey('S','V',5,{point_id:0,x:1,y:2}),'["target","S","V","5","0",1,2]');
});
run(`state.module={key:'BRAIN/mri-brain'};state.series={directory:'S'};state.variant={directory:'V'};
 state.capture={slice:{active_id:5},labels:[]};state.activeFilters=new Set(['9']);
 fixtureDefinition={identity_key:'1:7',taxon_id:'7',ta_id:1,name:'Source',latin:'Latin'};
 fixtureLabel={text:'Source',x:30,y:40,color:'#abc',text_align:'left',canvas:'C',label_index:0,filter_id:'9',binding_verified:true,definition:fixtureDefinition};
 state.languagePack={structures:{'1:7':{status:'reviewed',source:{name:'Source'},translation:{name:'Mẫu thử'}}}};
 state.anatomyLanguage='en-vi';el.annotationLayer=new Element('svg');el.anatomyViewport=new Element();
 targetForLabel=()=>null;`);
test('same scoped identity & coordinates across modes; never guess fragments',()=>{
  const before=run('JSON.stringify(fixtureLabel)');
  for(const lang of ['en','vi','en-vi','ja']){
    run(`state.anatomyLanguage='${lang}'`);
    assert.equal(run('structureFromLabel(fixtureLabel).key'),'taxon:1:7');
    assert.equal(run('labelValue({...fixtureLabel,text:"Sou"}).translated'),false);
    assert.equal(run('labelValue({...fixtureLabel,binding_verified:false}).translated'),false);
    assert.equal(run('labelValue({...fixtureLabel,definition:{...fixtureDefinition,identity_key:"2:7"}}).translated'),false);
  }
  assert.equal(run('JSON.stringify(fixtureLabel)'),before);
});
test('SVG bilingual tspan touch uses parent identity; both lines highlight together',()=>{
  run(`state.anatomyLanguage='en-vi';renderVisibleLabel(fixtureLabel,'taxon:1:7');`);
  assert.equal(run('el.annotationLayer.children[0].children.length'),2);
  assert.equal(run('el.annotationLayer.children[0].children[1].textContent'),'Mẫu thử');
  assert.equal(run('anatomyItemAt(el.annotationLayer.children[0].children[1]).key'),'taxon:1:7');
  assert.equal(run('el.annotationLayer.children[0].attrs["aria-pressed"]'),'true');
  assert.equal(run('el.annotationLayer.children[0].attrs.x'),'30');
  assert.equal(run('el.annotationLayer.children[0].attrs.y'),'40');
});
test('module description-only search results are not removed by name-only refilter',()=>{
  run(`el.structureSearch={value:'description-only token'};el.structureList=new Element();el.structureCount=new Element();el.structureEmpty=new Element();
    state.structureMode='search';renderStructureRows([{key:'taxon:1:7',name:'Source',definition:fixtureDefinition}]);`);
  assert.equal(run('el.structureList.children.length'),1);
});
test('language repaint preserves mobile hold, selection, image/metadata caches and view',()=>{
  run(`hideTooltip=()=>{};renderFilters=()=>{};renderOverlay=()=>{};renderSliceStructures=()=>{};
    definitionCalls=0;renderDefinition=()=>definitionCalls++;state.structureMode='slice';
    state.selectedStructure=structureFromLabel(fixtureLabel);state.selectionHighlightOnly=true;state.definitionPeek=false;
    state.zoom=2.5;state.panX=14;state.panY=25;sliceResourceCache.set('sentinel',{status:'ready'});repaintAnatomyLanguage();`);
  assert.equal(run('definitionCalls'),0);assert.equal(run('state.selectionHighlightOnly'),true);
  assert.equal(run('state.selectedStructure.key'),'taxon:1:7');
  assert.equal(run('state.zoom'),2.5);assert.equal(run('state.panX'),14);assert.equal(run('state.panY'),25);
  assert.equal(run("sliceResourceCache.get('sentinel').status"),'ready');
  run('state.selectionHighlightOnly=false;repaintAnatomyLanguage()');assert.equal(run('definitionCalls'),1);
});
async function asyncTests() {
  run(`pendingPacks=[];api=(url,query)=>new Promise(resolve=>pendingPacks.push({url,query,resolve}));
    el.anatomyLanguageSelect=new Element('select');el.anatomyLanguageStatus=new Element();
    state.anatomyLanguage='vi';slowPack=loadAnatomyLanguage({repaint:false});
    state.anatomyLanguage='en-vi';fastPack=loadAnatomyLanguage({repaint:false});`);
  assert.equal(run('pendingPacks[1].query.lang'),'vi');
  run(`pendingPacks[1].resolve({status:'available',marker:'new'});`);await run('fastPack');
  run(`pendingPacks[0].resolve({status:'available',marker:'stale'});`);await run('slowPack');
  assert.equal(run('state.languagePack.marker'),'new');
  run(`movingPack=loadAnatomyLanguage({repaint:false});state.module={key:'THORAX/other'};pendingPacks[2].resolve({marker:'wrong_module'});`);
  await run('movingPack');assert.equal(run('state.languagePack'),null);
  tests++;console.log('PASS delayed response never overrides new language or module');
  console.log(`LANGUAGE_DOM=PASS; tests=${tests}; synthetic_only`);
}
asyncTests().catch(e=>{console.error(e);process.exitCode=1;});
