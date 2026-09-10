// Focused teaching-form tests: network failure, duplicate clicks, stale previews.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
  constructor(id='') { this.id=id;this.value='';this.dataset={};this.children=[];this.events={};this.disabled=false;this.hidden=false;this.textContent='';this.classList={toggle(){}}; }
  addEventListener(name,fn) { (this.events[name] ||= []).push(fn); }
  fire(name) { const event={preventDefault(){}};return Promise.all((this.events[name]||[]).map(fn=>fn(event))); }
  appendChild(node) { this.children.push(node); }
  replaceChildren() { this.children=[]; }
  focus() {}
  scrollIntoView() {}
  set innerHTML(_) { throw new Error('Never interpolate taught RAW as HTML'); }
}
function setup(canTeach=true) {
  const ids={};for(const id of ['qr-learning','qrl-form','qrl-raw','qrl-output','qrl-scope','qrl-status','qrl-comparison','qrl-rules','qrl-count','qrl-initial-state','qrl-apply','qrl-preview','qrl-new','qrl-reload','qrl-before','qrl-after','qrl-form-title']) ids[id]=new Element(id);
  ids['qr-learning'].dataset.canTeach=canTeach?'1':'0';
  ids['qrl-form'].dataset={previewUrl:'/preview',saveUrl:'/save',listUrl:'/list'};
  ids['qrl-form'].querySelector=()=>({value:'csrf-test'});
  ids['qrl-form'].reportValidity=()=>!!(ids['qrl-raw'].value && ids['qrl-output'].value);
  ids['qrl-form'].reset=()=>{ids['qrl-raw'].value='';ids['qrl-output'].value='';ids['qrl-scope'].value='auto';};
  ids['qr-learning'].querySelectorAll=()=>[ids['qrl-apply'],ids['qrl-preview'],ids['qrl-new'],ids['qrl-reload'],ids['qrl-raw'],ids['qrl-output'],ids['qrl-scope']];
  ids['qrl-scope'].value='auto';ids['qrl-initial-state'].textContent=JSON.stringify({revision:0,rules:[]});
  const calls=[],storage=[];
  const context={document:{getElementById:id=>ids[id],createElement:()=>new Element()},
    localStorage:{setItem:(...args)=>storage.push(args)},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}))};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/js/qr_learning.js'),'utf8'),context);
  function fill(raw='Ngh)a',output='Nghĩa') { ids['qrl-raw'].value=raw;ids['qrl-output'].value=output;ids['qrl-form'].fire('input'); }
  function respond(index,body,ok=true) { calls[index].resolve({ok,status:ok?200:409,json:async()=>body}); }
  return {ids,calls,storage,fill,respond};
}
test('Xem thử sends no save request and ignores a response after input changes',async()=>{
  const u=setup();u.fill();const task=u.ids['qrl-preview'].fire('click');
  assert.equal(u.calls[0].url,'/preview');u.fill('Th╦','Thị');
  u.respond(0,{before:'old',after:'Nghĩa',scope:'Họ tên'});await task;
  assert.equal(u.ids['qrl-after'].textContent,'');assert.equal(u.calls.length,1);
});
test('Double click creates one write; successful save updates the list and notifies other tabs',async()=>{
  const u=setup();u.fill();const task=u.ids['qrl-form'].fire('submit');
  await u.ids['qrl-form'].fire('submit');assert.equal(u.calls.length,1);
  assert.equal(u.ids['qrl-apply'].disabled,true);
  assert.equal(u.calls[0].options.headers['X-CSRFToken'],'csrf-test');
  u.respond(0,{revision:1,applied_id:'a',rules:[{id:'a',raw:'Ngh)a',output:'Nghĩa',scope:'ho_ten',active:true}],message:'Đã học'});
  await task;assert.equal(u.ids['qrl-rules'].children.length,1);assert.equal(u.ids['qrl-status'].textContent,'Đã học');
  assert.equal(u.storage.length,1);assert.equal(u.storage[0][0],'khbl-qr-learning-revision');
  assert.equal(u.ids['qrl-apply'].disabled,false);assert.equal(u.ids['qrl-output'].value,'Nghĩa');
  const edit=u.ids['qrl-form'].fire('submit');assert.equal(JSON.parse(u.calls[1].options.body).edit_id,'a');
  u.respond(1,{revision:1,applied_id:'a',rules:[{id:'a',raw:'Ngh)a',output:'Nghĩa',scope:'ho_ten',active:true}],message:'Đã tồn tại'});await edit;
});
test('Write failure keeps entered values and allows retry without claiming success',async()=>{
  const u=setup();u.fill();const task=u.ids['qrl-form'].fire('submit');
  u.respond(0,{error:'Bộ mẫu vừa thay đổi'},false);await task;
  assert.equal(u.ids['qrl-raw'].value,'Ngh)a');assert.equal(u.ids['qrl-output'].value,'Nghĩa');
  assert.equal(u.ids['qrl-apply'].disabled,false);assert.match(u.ids['qrl-status'].textContent,/vừa thay đổi/);
  assert.equal(u.storage.length,0);
});
test('Read-only user cannot submit a teaching write',async()=>{
  const u=setup(false);u.fill();await u.ids['qrl-form'].fire('submit');assert.equal(u.calls.length,0);
});
