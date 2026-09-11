// Focused teaching-form tests: network failure, duplicate clicks, stale previews.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element {
  constructor(id='') { this.id=id;this.value='';this.dataset={};this.children=[];this.events={};this.disabled=false;this.hidden=false;this.textContent='';this.classList={toggle(){}}; }
  addEventListener(name,fn) { (this.events[name] ||= []).push(fn); }
  fire(name) { const event={target:this,key:'',preventDefault(){}};return Promise.all((this.events[name]||[]).map(fn=>fn(event))); }
  appendChild(node) { this.children.push(node); }
  replaceChildren() { this.children=[]; }
  focus() {}
  scrollIntoView() {}
  setAttribute(key,value) { this[key]=value; }
  set innerHTML(_) { throw new Error('Never interpolate taught RAW as HTML'); }
}
function setup(canTeach=true) {
  const ids={};for(const id of ['qr-learning','qrl-form','qrl-raw','qrl-output','qrl-scope','qrl-status','qrl-comparison','qrl-rules','qrl-count','qrl-initial-state','qrl-apply','qrl-preview','qrl-new','qrl-reload','qrl-before','qrl-after','qrl-form-title','qrl-translate','qrl-filter','qrl-undo','qrl-fragments','qrl-fragment-list','qrl-save-draft','qrl-draft-count','qrl-library-modal','qrl-library-subtitle','qrl-open-waiting','qrl-open-learned','qrl-tab-waiting','qrl-tab-learned','qrl-close-library','qrl-modal-draft-count','qrl-modal-rule-count']) ids[id]=new Element(id);
  ids['qr-learning'].dataset.canTeach=canTeach?'1':'0';
  ids['qrl-form'].dataset={previewUrl:'/preview',saveUrl:'/save',draftUrl:'/draft',listUrl:'/list',translateUrl:'/translate',filterUrl:'/filter'};
  ids['qrl-form'].querySelector=()=>({value:'csrf-test'});
  ids['qrl-form'].reportValidity=()=>!!(ids['qrl-raw'].value && ids['qrl-output'].value);
  ids['qrl-form'].reset=()=>{ids['qrl-raw'].value='';ids['qrl-output'].value='';ids['qrl-scope'].value='auto';};
  ids['qr-learning'].querySelectorAll=()=>Object.values(ids).filter(el=>el.id.startsWith('qrl-'));
  ids['qrl-library-modal'].hidden=true;
  ids['qrl-save-draft'].dataset.write='1';
  ids['qrl-scope'].value='auto';ids['qrl-initial-state'].textContent=JSON.stringify({revision:0,rules:[],drafts:[]});
  const calls=[],storage=[];
  const context={document:{getElementById:id=>ids[id],createElement:()=>new Element(),addEventListener(){}},
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

test('Dịch accepts an empty output, fills the server suggestion, and can undo without writing',async()=>{
  const u=setup(false);u.fill('Kh≤m 4','');const task=u.ids['qrl-translate'].fire('click');
  assert.equal(u.calls[0].url,'/translate');
  await u.ids['qrl-translate'].fire('click');assert.equal(u.calls.length,1);
  u.respond(0,{output:'Khóm 4',warnings:[]});await task;
  assert.equal(u.ids['qrl-raw'].value,'Kh≤m 4');assert.equal(u.ids['qrl-output'].value,'Khóm 4');
  assert.equal(u.storage.length,0);assert.equal(u.ids['qrl-apply'].disabled,true);
  await u.ids['qrl-undo'].fire('click');assert.equal(u.ids['qrl-output'].value,'');
});

test('LỌC selects aligned pairs, preserves edits when switching, and restores the full pair',async()=>{
  const u=setup();u.fill('Kh≤m 4 Ngh)a Th☃n','Khóm 4 Ngh)a Th☃n');
  const task=u.ids['qrl-filter'].fire('click');assert.equal(u.calls[0].url,'/filter');
  u.respond(0,{segments:[{raw:'Ngh)a',output:'Ngh)a',scope:'text',alignment:'token'},
    {raw:'Th☃n',output:'Th☃n',scope:'text',alignment:'token'}],warnings:[]});await task;
  assert.equal(u.ids['qrl-raw'].value,'Ngh)a');assert.equal(u.ids['qrl-output'].value,'Ngh)a');
  u.ids['qrl-output'].value='Nghĩa';
  await u.ids['qrl-fragment-list'].children[1].fire('click');
  assert.equal(u.ids['qrl-raw'].value,'Th☃n');
  await u.ids['qrl-fragment-list'].children[0].fire('click');
  assert.equal(u.ids['qrl-output'].value,'Nghĩa');
  assert.equal(u.calls.length,1);assert.equal(u.storage.length,0);
  await u.ids['qrl-undo'].fire('click');
  assert.equal(u.ids['qrl-raw'].value,'Kh≤m 4 Ngh)a Th☃n');
  assert.equal(u.ids['qrl-output'].value,'Khóm 4 Ngh)a Th☃n');
});

test('Filter with no issues or network failure preserves both input fields',async()=>{
  const u=setup();u.fill('Kh≤m 4','Khóm 4');const task=u.ids['qrl-filter'].fire('click');
  u.respond(0,{segments:[],warnings:[]});await task;
  assert.equal(u.ids['qrl-raw'].value,'Kh≤m 4');assert.equal(u.ids['qrl-output'].value,'Khóm 4');
  const failed=u.ids['qrl-translate'].fire('click');u.calls[1].reject(new Error('Mất kết nối'));await failed;
  assert.equal(u.ids['qrl-output'].value,'Khóm 4');assert.match(u.ids['qrl-status'].textContent,/Mất kết nối/);
});

test('Stale translation never overwrites newly edited text',async()=>{
  const u=setup();u.fill('Kh≤m 4','');const task=u.ids['qrl-translate'].fire('click');
  u.fill('RAW khác','Đã sửa');u.respond(0,{output:'Khóm 4',warnings:[]});await task;
  assert.equal(u.ids['qrl-output'].value,'Đã sửa');
});

test('Filtered fragment is taught as a new pair rather than overwriting the selected original rule',async()=>{
  const u=setup();u.fill('RAW đủ','Kết quả đủ');const save=u.ids['qrl-form'].fire('submit');
  u.respond(0,{revision:1,applied_id:'original',rules:[{id:'original',raw:'RAW đủ',output:'Kết quả đủ',scope:'dia_chi',active:true}],message:'OK'});await save;
  const filter=u.ids['qrl-filter'].fire('click');
  u.respond(1,{segments:[{raw:'Ngh)a',output:'Ngh)a',scope:'dia_chi'}],warnings:[]});await filter;
  u.ids['qrl-output'].value='Nghĩa';const next=u.ids['qrl-form'].fire('submit');
  const body=JSON.parse(u.calls[2].options.body);assert.equal(body.edit_id,'');assert.equal(body.raw,'Ngh)a');
  u.respond(2,{error:'Giữ lại để kiểm tra'},false);await next;
});

test('Lưu nháp needs only RAW, prevents double click, and adds one CHỜ without learning notice',async()=>{
  const u=setup();u.fill('RAW chưa xử lý','');
  const task=u.ids['qrl-save-draft'].fire('click');
  await u.ids['qrl-save-draft'].fire('click');assert.equal(u.calls.length,1);
  assert.equal(u.calls[0].url,'/draft');assert.equal(JSON.parse(u.calls[0].options.body).raw,'RAW chưa xử lý');
  u.respond(0,{revision:1,draft_id:'d1',drafts:[{id:'d1',raw:'RAW chưa xử lý',scope:'text'}],rules:[],message:'Đã lưu CHỜ'});await task;
  assert.equal(u.ids['qrl-draft-count'].textContent,1);assert.equal(u.storage.length,0);
  assert.equal(u.ids['qrl-output'].value,'');
});

test('Xử lý CHỜ then Áp dụng sends draft id and moves the item to ĐÃ HỌC',async()=>{
  const u=setup();u.fill('Ngh)a','');
  let task=u.ids['qrl-save-draft'].fire('click');
  u.respond(0,{revision:1,draft_id:'d1',drafts:[{id:'d1',raw:'Ngh)a',scope:'ho_ten'}],rules:[],message:'Đã lưu'});await task;
  await u.ids['qrl-open-waiting'].fire('click');assert.equal(u.ids['qrl-library-modal'].hidden,false);
  await u.ids['qrl-rules'].children[0].children[1].children[0].fire('click');
  assert.equal(u.ids['qrl-library-modal'].hidden,true);assert.equal(u.ids['qrl-raw'].value,'Ngh)a');
  task=u.ids['qrl-translate'].fire('click');u.respond(1,{output:'Nghĩa',warnings:[]});await task;
  task=u.ids['qrl-form'].fire('submit');
  assert.equal(JSON.parse(u.calls[2].options.body).draft_id,'d1');
  u.respond(2,{revision:2,applied_id:'r1',drafts:[],rules:[{id:'r1',raw:'Ngh)a',output:'Nghĩa',scope:'ho_ten',active:true}],message:'Đã chuyển'});await task;
  assert.equal(u.ids['qrl-draft-count'].textContent,0);assert.equal(u.ids['qrl-count'].textContent,'1/1');
  assert.equal(u.storage.length,1);
});

test('Compact manager switches CHỜ and ĐÃ HỌC in one modal',async()=>{
  const u=setup();
  await u.ids['qrl-open-learned'].fire('click');
  assert.equal(u.ids['qrl-library-modal'].hidden,false);
  assert.equal(u.ids['qrl-library-subtitle'].textContent,'Danh sách ĐÃ HỌC');
  await u.ids['qrl-tab-waiting'].fire('click');
  assert.equal(u.ids['qrl-library-subtitle'].textContent,'Danh sách CHỜ xử lý');
  await u.ids['qrl-close-library'].fire('click');assert.equal(u.ids['qrl-library-modal'].hidden,true);
});
