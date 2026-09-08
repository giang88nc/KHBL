const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/js/chuyen_khoan.js', 'utf8');

function setup({day='2026-09-08', hidden=false}={}) {
  const values={d1:day,d2:day};
  const listeners={}, bodyListeners={}, windowListeners={}, timers=new Map();
  const form={};
  const result={id:'ck-results',dataset:{signature:'sig',page:'1'}};
  const status={textContent:'',dataset:{url:'/banle/chuyen-khoan/doi-soat/',csrf:'token'}};
  const nodes={'ck-filter':form,'ck-results':result,'ck-status':{},'ck-reconcile-status':status};
  let calls=0, resolveFetch, timerId=0;
  const document={hidden, getElementById:id=>nodes[id], addEventListener:(n,f)=>listeners[n]=f,
    body:{addEventListener:(n,f)=>bodyListeners[n]=f}};
  class FixedDate extends Date {constructor(...args){super(...(args.length?args:['2026-09-08T05:00:00Z']));}}
  vm.runInNewContext(source, {
    document, location:{pathname:'/banle/chuyen-khoan/'},
    window:{addEventListener:(n,f)=>windowListeners[n]=f},
    Intl, Date:FixedDate, URLSearchParams, AbortController,
    FormData:class {constructor(){return Object.entries(values);}},
    htmx:{ajax:async()=>{},trigger:()=>{}},
    setInterval:()=>1, clearInterval:()=>{},
    setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;},
    clearTimeout:id=>timers.delete(id),
    fetch:(url,opts)=>{calls++;assert.equal(opts.method,'POST');assert.equal(opts.headers['X-CSRFToken'],'token');
      return new Promise(resolve=>resolveFetch=()=>resolve({json:async()=>({status:'ok',matched:0,pending:1})}));}
  });
  return {document,listeners,windowListeners,timers,status,get calls(){return calls;},
    finish:async()=>{resolveFetch();await new Promise(r=>setImmediate(r));},
    filter:newDay=>{values.d2=newDay;bodyListeners['htmx:beforeRequest']({detail:{elt:form}});
      bodyListeners['htmx:afterRequest']({detail:{elt:form,target:result,successful:true}});}};
}

test('ngày cũ hoặc tab ẩn không gọi đối soát lần đầu',()=>{
  assert.equal(setup({day:'2026-09-07'}).calls,0);
  assert.equal(setup({hidden:true}).calls,0);
});
test('hôm nay gọi POST ngay, không chồng yêu cầu và hẹn 5 giây sau khi xong',async()=>{
  const s=setup();assert.equal(s.calls,1);
  s.windowListeners.pageshow();s.listeners.visibilitychange();assert.equal(s.calls,1);
  await s.finish();assert.equal([...s.timers.values()].filter(t=>t.ms===5000).length,1);
});
test('đổi bộ lọc sang ngày cũ dừng lượt định kỳ',async()=>{
  const s=setup();await s.finish();s.filter('2026-09-07');
  assert.equal([...s.timers.values()].filter(t=>t.ms===5000).length,0);
  assert.equal(s.calls,1);
});
test('đổi bộ lọc về hôm nay gọi ngay',()=>{
  const s=setup({day:'2026-09-07'});s.filter('2026-09-08');assert.equal(s.calls,1);
});
test('ẩn tab dừng lịch, hiện lại tiếp tục',async()=>{
  const s=setup();await s.finish();s.document.hidden=true;s.listeners.visibilitychange();
  assert.equal([...s.timers.values()].filter(t=>t.ms===5000).length,0);
  s.document.hidden=false;s.listeners.visibilitychange();assert.equal(s.calls,2);
});
test('rời trang dừng lịch đối soát',async()=>{
  const s=setup();await s.finish();s.windowListeners.pagehide();
  assert.equal([...s.timers.values()].filter(t=>t.ms===5000).length,0);
});
