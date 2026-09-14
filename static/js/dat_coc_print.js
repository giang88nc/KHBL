(() => {
  'use strict';
  const host=document.getElementById('receipt-pages'), button=document.getElementById('print-receipt'), status=document.getElementById('print-status');
  const configuring=new URLSearchParams(location.search).get('configure')==='1';
  if(configuring) document.body.classList.add('config-preview');
  document.getElementById('paper-background')?.addEventListener('change',e=>document.body.classList.toggle('hide-paper',!e.target.checked));
  function print(){if(document.body.dataset.printReady==='true') window.print();}
  button?.addEventListener('click',print);
  if(!host||!button)return;
  const paper=host.querySelector('.deposit-paper');
  const originalRows=[...paper.querySelectorAll('[data-fit-area] tbody')].map(body=>[...body.children].map(row=>row.cloneNode(true)));
  function restoreRows(){
    host.querySelectorAll('[data-continuation]').forEach(page=>page.remove());
    paper.querySelectorAll('[data-fit-area] tbody').forEach((body,i)=>body.replaceChildren(...originalRows[i].map(row=>row.cloneNode(true))));
  }
  function fitsTables(page){return [...page.querySelectorAll('[data-fit-area]')].every(area=>area.clientHeight>0&&area.scrollHeight<=area.clientHeight+1&&area.scrollWidth<=area.clientWidth+1);}
  function paginate(){
    let current=paper,index=0,pages=1;
    const count=originalRows[0].length;
    while(index<count){
      const bodies=[...current.querySelectorAll('[data-fit-area] tbody')];bodies.forEach(body=>body.replaceChildren());
      let added=0;
      while(index<count){
        bodies.forEach((body,i)=>body.append(originalRows[i][index].cloneNode(true)));
        // Keep even an oversized single row: printing is allowed at the user's request.
        if(added>0&&!fitsTables(current)){bodies.forEach(body=>body.lastElementChild.remove());break;}
        added++;index++;
      }
      if(index<count){current=paper.cloneNode(true);current.dataset.continuation='true';current.querySelectorAll('.block-selected').forEach(el=>el.classList.remove('block-selected'));host.append(current);pages++;}
    }
    return pages;
  }
  function report(ok,message){document.body.dataset.printReady=String(ok);button.disabled=!ok;status.textContent=message;status.classList.toggle('error',!ok);
    if(configuring&&parent!==window)parent.postMessage({depositPrint:'fit',ok,message},location.origin);}
  function fit(){
    button.disabled=true;
    restoreRows();
    let ok=true,shrunk=false;
    for(const area of paper.querySelectorAll('[data-fit-area]')){
      const table=area.querySelector('table');table.style.zoom='1';
      const fits=()=>area.clientHeight>0&&area.scrollHeight<=area.clientHeight+1&&area.scrollWidth<=area.clientWidth+1;
      let scale=1;
      while(!fits()&&scale>.6){scale=Math.max(.6,Math.round((scale-.02)*100)/100);table.style.zoom=String(scale);}
      if(!fits())ok=false;if(scale<1)shrunk=true;
    }
    const rect=paper.getBoundingClientRect();
    for(const el of paper.querySelectorAll('[data-dc-block]')){
      const r=el.getBoundingClientRect();
      if(r.left<rect.left-.5||r.top<rect.top-.5||r.right>rect.right+.5||r.bottom>rect.bottom+.5)ok=false;
      if(!el.matches('[data-fit-area],.store-copies')&&(el.scrollHeight>el.clientHeight+1||el.scrollWidth>el.clientWidth+1))ok=false;
    }
    for(const el of paper.querySelectorAll('.store-copy header,.store-employee'))if(el.scrollWidth>el.clientWidth+1)ok=false;
    const pages=!fitsTables(paper)&&originalRows[0].length?paginate():1;
    report(true,pages>1?`Xem trước ${pages} trang · Chỉ in trang 1; phần từ trang 2 không được in.`:ok?`1 phiếu · 1 trang in${shrunk?' · Đã thu gọn bảng món':''}`:'Nội dung vượt vùng in · Vẫn cho in, chỉ in phần nằm trên trang 1.');
  }
  document.fonts.ready.then(fit).catch(e=>report(false,e.message));
  if(!configuring)return;
  window.addEventListener('message',e=>{
    if(e.origin!==location.origin||e.source!==parent)return;
    if(e.data?.depositPrint==='layout'&&typeof e.data.css==='string'){
      document.getElementById('deposit-layout-css').textContent=e.data.css;requestAnimationFrame(fit);
    }else if(e.data?.depositPrint==='print')print();
    else if(e.data?.depositPrint==='select')paper.querySelectorAll('[data-dc-block]').forEach(el=>el.classList.toggle('block-selected',el.dataset.dcBlock===e.data.key));
  });
  let drag=null;
  paper.addEventListener('pointerdown',e=>{
    const block=e.target.closest('[data-dc-block]');if(!block)return;e.preventDefault();
    const r=paper.getBoundingClientRect(),b=block.getBoundingClientRect();
    drag={key:block.dataset.dcBlock,x:e.clientX,y:e.clientY,left:(b.left-r.left)/r.width*100,top:(b.top-r.top)/r.height*100,w:r.width,h:r.height};
    paper.setPointerCapture(e.pointerId);parent.postMessage({depositPrint:'select',key:drag.key},location.origin);
  });
  paper.addEventListener('pointermove',e=>{if(drag)parent.postMessage({depositPrint:'move',key:drag.key,left:drag.left+(e.clientX-drag.x)/drag.w*100,top:drag.top+(e.clientY-drag.y)/drag.h*100},location.origin);});
  paper.addEventListener('pointerup',()=>{drag=null;});
  paper.addEventListener('pointercancel',()=>{drag=null;});
})();
