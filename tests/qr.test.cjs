// node --test tests/qr.test.cjs — không mạng / CSDL; chạy đúng mã production với DOM và đồng hồ giả.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'qr_cases.json'), 'utf8'));
function parser() {
  const context = {window:{}, TextDecoder, Uint8Array};
  vm.createContext(context);
  for (const file of ['vn_text.js', 'cccd.js'])
    vm.runInContext(fs.readFileSync(path.join(root, 'static/js', file), 'utf8'), context);
  return context;
}
for (const c of cases) test(c.name, () => {
  const r = parser().window.CCCD.parse(c.raw);
  if (c.invalid) { assert.equal(r.data, null); assert.ok(r.loi.length); }
  else {
    assert.equal(r.loi.length, 0);
    for (const [k,v] of Object.entries(c.expect)) assert.equal(r.data[k], v);
    assert.equal(!!r.canh_bao.length, c.warning);
  }
});
test('Giữ nguyên số trong chuỗi chữ', () => {
  for (const s of ['012345678901', '01022000', 'Số 0123', 'Số 0198', 'Số 022501870141', '0198A', 'Giá trị']) {
    const r = parser().window.VNText.chuanHoa(s); assert.equal(r.text,s); assert.equal(r.canhBao.length,0);
  }
});

class Element {
  constructor(tag='div') { this.tagName=tag; this.value=''; this.dataset={}; this.style={}; this.children=[]; this.events={}; this.isConnected=true; this.selected=0; }
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
  fire(name, extra={}) {
    const e={target:this, prevented:false, preventDefault(){this.prevented=true;},stopPropagation(){},stopImmediatePropagation(){},...extra};
    for (const fn of this.events[name] || []) fn(e); return e;
  }
  appendChild(el) { this.children.push(el); }
  replaceChildren() { this.children=[]; }
  set innerHTML(value) { throw new Error('QR không được ghép innerHTML'); }
  set textContent(value) { this.text=value; this.children=[]; }
  get textContent() { return this.text || ''; }
  focus() { this.focused=true; }
  select() { this.selected++; }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this[name] = value; }
}
function ui() {
  const ctx=parser(), ids={}, timers=new Map(); let clock=0;
  const windowEvents={};ctx.window.addEventListener=(name,fn)=>{windowEvents[name]=fn;};
  ctx.window.removeEventListener=name=>{delete windowEvents[name];};ctx.windowEvents=windowEvents;
  for (const id of ['kh-qr-in','kh-qr-msg','kh-qr-preview','kh-qr-btn','kh-qr-discard','kh-qr-more','f-ten','f-cccd','f-sinh','f-gt','f-dc','f-cap']) ids[id]=new Element();
  const form=new Element('form'), modal=new Element(), save=new Element('button');
  form.querySelector=s=>ids[s.slice(1)] || null;
  modal.querySelector=()=>save;
  ids['kh-qr-in'].closest=s=>s==='form'?form:modal;
  ctx.document={addEventListener(){},getElementById(){return null;},createElement:tag=>new Element(tag)};
  ctx.setTimeout=fn=>{timers.set(++clock,fn);return clock;}; ctx.clearTimeout=id=>timers.delete(id);
  vm.runInContext(fs.readFileSync(path.join(root,'static/js/khbl.js'),'utf8'),ctx);
  const host=new Element(); host.querySelector=s=>s==='#kh-qr-in'?ids['kh-qr-in']:null;
  ctx.window.khblBind(host); ctx.window.khblBind(host);
  const input=raw=>{ids['kh-qr-in'].value=raw;ids['kh-qr-in'].fire('input');};
  const enter=()=>ids['kh-qr-in'].fire('keydown',{key:'Enter'});
  const flush=()=>{const pending=[...timers.values()];timers.clear();pending.forEach(f=>f());};
  return {ids,form,save,timers,input,enter,flush,ctx};
}
const valid=cases[0].raw;
test('Enter điền đủ một lần và hủy bộ hẹn giờ',()=>{
  const u=ui(); u.input(valid); assert.equal(u.save.disabled,true); assert.equal(u.timers.size,1);
  assert.equal(u.enter().prevented,true); assert.equal(u.timers.size,0); assert.equal(u.save.disabled,false);
  assert.equal(u.ids['f-cccd'].value,'012345678901');assert.equal(u.ids['f-sinh'].value,'2000-02-01');
  u.ids['f-ten'].value='Tên sửa tay';u.flush();assert.equal(u.ids['f-ten'].value,'Tên sửa tay');
  assert.equal(u.ids['kh-qr-in'].events.input.length,1);
});
test('Ngắt nhịp không điền sớm hoặc chọn toàn bộ ô quét',()=>{
  const u=ui();u.input(valid.split('|').slice(0,6).join('|'));u.flush();
  assert.equal(u.ids['f-ten'].value,'');assert.equal(u.ids['kh-qr-in'].selected,0);
  u.input(valid.slice(0,-2));u.flush();assert.equal(u.ids['f-ten'].value,'');
  u.input(valid);u.flush();assert.equal(u.ids['f-ten'].value,'');
  assert.equal(u.ids['kh-qr-preview'].children.length,1);assert.equal(u.ids['kh-qr-in'].selected,0);
  u.ids['kh-qr-btn'].fire('click');assert.equal(u.ids['f-ten'].value,'Nguyen Van A');
});
test('Quét thẻ thứ hai thiếu trường: xem trước rồi xóa sạch trường cũ',()=>{
  const u=ui();u.input(valid);u.enter();u.input(cases[14].raw);u.enter();
  assert.equal(u.ids['f-ten'].value,'Nguyen Van A');assert.equal(u.save.disabled,true);
  u.ids['kh-qr-btn'].fire('click');assert.equal(u.ids['f-ten'].value,'Nguyen Van B');
  for (const id of ['f-sinh','f-gt','f-dc','f-cap']) assert.equal(u.ids[id].value,'');
  assert.equal(u.save.disabled,false);
});
test('Chuỗi lỗi không sửa form; bỏ kết quả trả lại quyền lưu',()=>{
  const u=ui();u.input(valid);u.enter();u.input(cases[4].raw);u.enter();
  assert.equal(u.ids['f-ten'].value,'Nguyen Van A');assert.equal(u.form.fire('submit').prevented,true);
  u.ids['kh-qr-discard'].fire('click');assert.equal(u.ids['f-ten'].value,'Nguyen Van A');assert.equal(u.save.disabled,false);
});
test('Đóng popup trước timeout không ghi vào form khác',()=>{
  const u=ui();u.input(valid);u.ids['kh-qr-in'].isConnected=false;u.flush();assert.equal(u.ids['f-ten'].value,'');
});
test('Nội dung HTML bị từ chối mà không tạo HTML từ chuỗi quét',()=>{
  const u=ui();u.input(cases[21].raw);u.enter();assert.equal(u.ids['f-ten'].value,'');assert.equal(u.save.disabled,true);
});
test('Thay đổi form sau xem trước phải đối chiếu lại',()=>{
  const u=ui();u.input(valid);u.flush();u.ids['f-ten'].value='Khách đang sửa';
  u.form.fire('input',{target:u.ids['f-ten']});u.ids['kh-qr-btn'].fire('click');
  assert.equal(u.ids['f-ten'].value,'Khách đang sửa');assert.equal(u.save.disabled,true);
});

test('RAW gửi tới server, kết quả cũ không đè lượt quét mới',async()=>{
  const u=ui(), replies=[];
  u.ids['kh-qr-in'].dataset.analyzeUrl='/banle/khach-hang/qr/phan-tich/';
  u.ctx.window.CCCD.analyze=(url,scans)=>new Promise(resolve=>replies.push({scans,resolve}));
  u.input(valid);u.enter();
  const next=valid.replace('NGUYEN VAN A','NGUYEN VAN B');u.input(next);u.enter();
  replies[0].resolve(u.ctx.window.CCCD.parse(valid));await Promise.resolve();
  assert.equal(u.ids['f-ten'].value,'');
  replies[1].resolve(u.ctx.window.CCCD.parse(next));await Promise.resolve();
  assert.equal(u.ids['f-ten'].value,'Nguyen Van B');
});
test('Lỗi kết nối giữ form và chặn lưu, không tự rơi về suy đoán cục bộ',async()=>{
  const u=ui();u.ids['kh-qr-in'].dataset.analyzeUrl='/analyze';
  u.ctx.window.CCCD.analyze=()=>Promise.reject(new Error('Không kết nối'));
  u.input(valid);u.enter();await Promise.resolve();await Promise.resolve();
  assert.equal(u.ids['f-ten'].value,'');assert.equal(u.save.disabled,true);
  assert.match(u.ids['kh-qr-msg'].textContent,/Không kết nối/);
});
test('Quét thêm gửi nguyên cả hai RAW, gợi ý chỉ áp dụng sau đối chiếu',async()=>{
  const u=ui(), sent=[];u.ids['kh-qr-in'].dataset.analyzeUrl='/analyze';
  const next=valid.replace('NGUYEN VAN A','Nguyễn Văn A');
  u.ctx.window.CCCD.analyze=async(url,scans)=>{
    sent.push(scans);const result=u.ctx.window.CCCD.parse(scans[scans.length-1]);
    result.raw_scans=scans;result.canh_bao=['Cần đối chiếu'];result.data.canh_bao=result.canh_bao;
    return result;
  };
  u.input(valid);u.enter();await Promise.resolve();
  u.ids['kh-qr-more'].fire('click');u.input(next);u.enter();await Promise.resolve();
  assert.equal(sent[1].length,2);assert.equal(sent[1][0],valid);assert.equal(sent[1][1],next);
  assert.equal(u.ids['f-ten'].value,'');u.ids['kh-qr-btn'].fire('click');
  assert.equal(u.ids['f-ten'].value,'Nguyễn Văn A');
});

test('Thiếu pipe hoặc có trường mở rộng vẫn gửi RAW tới engine sau khi ngừng quét',async()=>{
  for (const raw of [valid.replaceAll('|',''), valid+'|||scanner-3|extension']) {
    const u=ui(), sent=[];u.ids['kh-qr-in'].dataset.analyzeUrl='/analyze';
    u.ctx.window.CCCD.analyze=async(url,scans)=>{
      sent.push(scans[0]);const r=u.ctx.window.CCCD.parse(valid);
      r.canh_bao=['Đã phục hồi ranh giới'];r.data.canh_bao=r.canh_bao;return r;
    };
    u.input(raw);u.flush();await Promise.resolve();
    assert.equal(sent[0],raw);assert.equal(u.ids['f-ten'].value,'');
    assert.equal(u.save.disabled,true);
    u.ids['kh-qr-btn'].fire('click');assert.equal(u.ids['f-ten'].value,'Nguyen Van A');
  }
});

test('Gợi ý theo mẫu đã duyệt đứng đầu; đổi lựa chọn chỉ có hiệu lực sau Áp dụng',async()=>{
  const u=ui();u.ids['kh-qr-in'].dataset.analyzeUrl='/analyze';
  u.ctx.window.CCCD.analyze=async()=>{
    const r=u.ctx.window.CCCD.parse(valid);r.data.ho_ten='Tên đã duyệt';
    r.canh_bao=['Khớp mẫu cột D'];r.data.canh_bao=r.canh_bao;
    r.suggestions={ho_ten:[{value:'Tên đã duyệt'},{value:'Phương án khác'}]};return r;
  };
  u.input(valid);u.enter();await Promise.resolve();
  const select=u.ids['kh-qr-preview'].children[0].children[1].children[2].children[0];
  assert.equal(select.value,'Tên đã duyệt');assert.equal(u.ids['f-ten'].value,'');
  select.value='Phương án khác';select.fire('change');assert.equal(u.ids['f-ten'].value,'');
  u.ids['kh-qr-btn'].fire('click');assert.equal(u.ids['f-ten'].value,'Phương án khác');
});

test('Dạy mẫu ở tab khác làm mất hiệu lực kết quả cũ và cho phép phân tích cùng RAW lại',async()=>{
  const u=ui();let calls=0;u.ids['kh-qr-in'].dataset.analyzeUrl='/analyze';
  u.ctx.window.CCCD.analyze=async()=>{calls++;return u.ctx.window.CCCD.parse(valid);};
  u.input(valid);u.enter();await Promise.resolve();assert.equal(calls,1);assert.equal(u.save.disabled,false);
  u.ctx.windowEvents.storage({key:'khbl-qr-learning-revision'});assert.equal(u.save.disabled,true);
  u.enter();await Promise.resolve();assert.equal(calls,2);
});
