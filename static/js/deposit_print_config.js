(() => {
  const root=document.getElementById('dc-layout-config'),frame=document.getElementById('dc-layout-frame'),status=document.getElementById('dc-layout-status');
  const blocks=JSON.parse(document.getElementById('dc-layout-blocks').textContent),defaults=JSON.parse(document.getElementById('dc-layout-defaults').textContent);
  let state=JSON.parse(document.getElementById('dc-layout-state').textContent),selected=null,ready=false;
  const clone=x=>JSON.parse(JSON.stringify(x)),round=x=>Math.round(x*100)/100,limit=(x,a,b)=>Math.max(a,Math.min(b,x));
  function tell(data){frame.contentWindow.postMessage(data,location.origin);}
  function css(){
    const rules=blocks.map(b=>{const v=state[b.key];return `${b.sel}{position:absolute!important;left:${v.left}%!important;top:${v.top}%!important;width:${v.w}%!important;height:${v.h}%!important;--dc-font:${v.fs}pt;}`;});
    const i=state._in,k={auto:'auto',A5:'A5 portrait',A4:'A4 portrait',Letter:'letter portrait'};
    rules.push(`@media print{@page{size:${k[i.kho]||'auto'};margin:0}.gdb-a5{left:${i.dx}mm!important;top:${i.dy}mm!important;margin:${i.canh==='trai'?'0':'0 auto'}!important;transform:scale(${i.ty_le/100})!important;transform-origin:top ${i.canh==='trai'?'left':'center'}!important}}`);
    return rules.join('\n');
  }
  function apply(){ready=false;document.getElementById('dc-layout-print').disabled=true;tell({depositPrint:'layout',css:css()});}
  function sync(){root.querySelectorAll('[data-key]').forEach(row=>{row.classList.toggle('selected',row.dataset.key===selected);row.querySelectorAll('[data-p]').forEach(input=>{if(document.activeElement!==input)input.value=state[row.dataset.key][input.dataset.p];});});root.querySelectorAll('[data-in]').forEach(input=>{if(document.activeElement!==input)input.value=state._in[input.dataset.in];});}
  function select(key){selected=key;sync();tell({depositPrint:'select',key});}
  function clampBox(key){const v=state[key];v.w=limit(v.w,1,100);v.h=limit(v.h,1,100);v.left=round(limit(v.left,0,100-v.w));v.top=round(limit(v.top,0,100-v.h));v.fs=limit(v.fs,5,24);}
  root.querySelectorAll('[data-key]').forEach(row=>{row.addEventListener('click',()=>select(row.dataset.key));row.querySelectorAll('[data-p]').forEach(input=>input.addEventListener('input',()=>{const n=Number(input.value);if(!Number.isFinite(n)||input.value==='')return;state[row.dataset.key][input.dataset.p]=round(n);clampBox(row.dataset.key);apply();sync();}));});
  root.querySelectorAll('[data-in]').forEach(input=>input.addEventListener('change',()=>{const key=input.dataset.in;if(['dx','dy','ty_le'].includes(key)){const n=Number(input.value);if(!Number.isFinite(n))return;state._in[key]=round(limit(n,key==='ty_le'?50:-80,key==='ty_le'?150:80));}else state._in[key]=input.value;apply();sync();}));
  frame.addEventListener('load',apply);
  window.addEventListener('message',e=>{if(e.origin!==location.origin||e.source!==frame.contentWindow)return;const d=e.data;if(d?.depositPrint==='fit'){ready=d.ok;status.textContent=d.message;status.classList.toggle('error',!d.ok);document.getElementById('dc-layout-print').disabled=!ready;}else if(d?.depositPrint==='select')select(d.key);else if(d?.depositPrint==='move'&&state[d.key]){state[d.key].left=d.left;state[d.key].top=d.top;clampBox(d.key);apply();sync();}});
  document.addEventListener('keydown',e=>{if(!selected||e.target.matches('input,select,textarea'))return;const v=state[selected],step=e.shiftKey?1:.2;if(e.key==='ArrowLeft')v.left-=step;else if(e.key==='ArrowRight')v.left+=step;else if(e.key==='ArrowUp')v.top-=step;else if(e.key==='ArrowDown')v.top+=step;else return;e.preventDefault();clampBox(selected);apply();sync();});
  async function save(){
    const button=document.getElementById('dc-layout-save');button.disabled=true;
    try{const response=await fetch(root.dataset.url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':root.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({layout:state})});const j=await response.json();if(!response.ok||!j.ok)throw Error(j.loi||'Không lưu được mẫu');state=j.layout;sync();apply();const toast=document.createElement('div');toast.className='success';toast.textContent='Đã lưu mẫu in CỌC.';document.getElementById('toast-root')?.append(toast);setTimeout(()=>toast.remove(),4000);return true;}catch(e){status.textContent=e.message;status.classList.add('error');return false;}finally{button.disabled=false;}
  }
  document.getElementById('dc-layout-save').addEventListener('click',save);
  document.getElementById('dc-layout-reset').addEventListener('click',()=>{state=clone(defaults);sync();apply();status.textContent='Đã xem mẫu mặc định. Bấm Lưu mẫu để áp dụng.';});
  document.getElementById('dc-layout-print').addEventListener('click',()=>{if(ready)tell({depositPrint:'print'});});
  sync();
  if(frame.contentDocument?.readyState==='complete')apply();
})();
