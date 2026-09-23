/* Popup đặt cọc: tính tiền số nguyên cố định, khách hàng và popup khách lồng. */
(() => {
  'use strict';
  const SCALE=100000000n, fmt=new Intl.NumberFormat('vi-VN');
  const field=(row,key)=>row.querySelector(`[name$="-${key}"]`);
  const form=()=>document.getElementById('dc-save');
  function notes() {try {const n=JSON.parse(form()?.elements.NoteEntries?.value||'[]');return Array.isArray(n)?n:[];}catch(_){return [];}}
  function renderNotes(entries=notes()) {
    const root=document.getElementById('dc-note-list'); if(!root) return;
    form().elements.NoteEntries.value=JSON.stringify(entries);root.replaceChildren();
    entries.forEach((entry,index)=>{
      const row=document.createElement('div');row.className='dc-note-entry';
      const text=document.createElement('span'), date=document.createElement('b'), content=document.createElement('span');
      date.textContent=entry.date?entry.date.split('-').reverse().join('/')+': ':'Ghi chú cũ: ';
      content.textContent=entry.text;text.append(date,content);
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.title='Bỏ dòng ghi chú khi lưu';remove.setAttribute('aria-label','Bỏ ghi chú '+(index+1));
      remove.onclick=()=>{const current=notes();current.splice(index,1);renderNotes(current);};row.append(text);
      if(index>=Number(form().dataset.savedNotes||0)) row.append(remove);root.append(row);
    });
    if(!entries.length){const empty=document.createElement('small');empty.textContent='Chưa có ghi chú.';root.append(empty);}
  }
  function addNote() {
    const input=form()?.elements.NoteText, text=input?.value.trim();if(!text) return;
    const entries=notes();entries.push({date:document.getElementById('dc-today').value,text});renderNotes(entries);input.value='';input.focus();
  }
  function hidePromise() {const menu=document.querySelector('.dc-promise-menu');if(menu) menu.hidden=true;document.querySelector('[data-dc-promise-toggle]')?.setAttribute('aria-expanded','false');}
  function unlockEditor() {
    const f=form(); if(!f||document.querySelector('.dc-passcode')) return;
    const dialog=document.createElement('dialog');dialog.className='dc-passcode';dialog.dataset.khblLopCon='';
    dialog.innerHTML='<form><h3>Xác nhận Passcode</h3><p>Mở sửa khách hàng, sản phẩm và cọc dự kiến của phiếu này.</p><label>Passcode<input type="password" name="passcode" autocomplete="off" required maxlength="128"></label><p role="alert" data-dc-passcode-error></p><div><button type="button" data-close>Đóng</button><button type="submit">Xác nhận</button></div></form>';
    document.body.append(dialog);dialog.showModal();
    const close=()=>{dialog.close();dialog.remove();};dialog.querySelector('[data-close]').onclick=close;dialog.addEventListener('cancel',close);
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault();const button=dialog.querySelector('[type=submit]');button.disabled=true;
      const data=new FormData();data.set('csrfmiddlewaretoken',f.elements.csrfmiddlewaretoken.value);data.set('token',f.elements.token.value);data.set('passcode',dialog.querySelector('input').value);
      try {
        const response=await fetch(f.dataset.unlockUrl,{method:'POST',body:data});const result=await response.json();
        if(!response.ok||!result.grant) throw Error(result.error||'Không xác nhận được Passcode.');
        if(form()!==f) {close();return;}
        f.elements.edit_grant.value=result.grant;f.dataset.editLocked='0';
        f.querySelectorAll('[data-dc-protected]').forEach(el=>el.disabled=false);
        ['CustID','CashDeposit','BankDeposit','TienCoc'].forEach(key=>f.elements[key].disabled=false);
        f.querySelector('[data-dc-unlock]').hidden=true;
        close();window.dcToast?.('Đã mở sửa phiếu. Ghi chú cũ vẫn được giữ nguyên.');
      } catch(error) {dialog.querySelector('[data-dc-passcode-error]').textContent=error.message;dialog.querySelector('input').value='';dialog.querySelector('input').focus();}
      finally {button.disabled=false;}
    };
  }
  let cameraStream, cameraDialog;
  function closeCamera() {
    cameraStream?.getTracks().forEach(t=>t.stop()); cameraStream=null;
    cameraDialog?.close(); cameraDialog?.remove(); cameraDialog=null;
  }
  async function openCamera(input) {
    if(cameraDialog) return;
    const dialog=document.createElement('dialog'); cameraDialog=dialog;
    dialog.className='dc-camera'; dialog.dataset.khblLopCon='';
    dialog.innerHTML='<h3>Chụp hình phiếu</h3><video autoplay playsinline muted></video><p>Cho phép dùng camera khi trình duyệt hỏi.</p><div><button type="button" data-camera-close>Đóng</button><button type="button" data-camera-shoot disabled>Chụp hình</button></div>';
    document.body.append(dialog); dialog.showModal();
    dialog.querySelector('[data-camera-close]').onclick=closeCamera; dialog.addEventListener('cancel',closeCamera);
    try {
      const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
      if(cameraDialog!==dialog) {stream.getTracks().forEach(t=>t.stop());return;}
      cameraStream=stream; const video=dialog.querySelector('video'); video.srcObject=stream;
      video.onloadedmetadata=()=>{dialog.querySelector('[data-camera-shoot]').disabled=false;dialog.querySelector('p').textContent='Đặt sản phẩm trong khung rồi bấm Chụp hình.';};
      dialog.querySelector('[data-camera-shoot]').onclick=()=>{
        const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;
        if(!canvas.width) return;
        canvas.getContext('2d').drawImage(video,0,0); canvas.toBlob(blob=>{
          if(!blob||!input.isConnected) return;
          const data=new DataTransfer();data.items.add(new File([blob],'hinh-chup.jpg',{type:'image/jpeg'}));input.files=data.files;
          input.dispatchEvent(new Event('change',{bubbles:true}));closeCamera();
        },'image/jpeg',.9);
      };
    } catch(_) {closeCamera();window.dcToast?.('Không mở được camera. Cho phép camera hoặc dùng nút Chọn để tải ảnh.','warning');}
  }
  function number(value) {
    const m=String(value ?? '').trim().replace(',','.').replace(/^\./,'0.').match(/^(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
    if (!m) return 0n;
    const exponent=Number(m[3]||0), fraction=m[2]||'', shift=8+exponent-fraction.length;
    if (Math.abs(shift)>40) return 0n;
    const n=BigInt(m[1]+fraction); return shift>=0 ? n*10n**BigInt(shift) : n/10n**BigInt(-shift);
  }
  function decimal(n) { return `${n/SCALE}.${String(n%SCALE).padStart(8,'0')}`.replace(/\.?0+$/,'') || '0'; }
  function money(n) { const whole=n/SCALE, part=String(n%SCALE).padStart(8,'0').replace(/0+$/,''); return fmt.format(whole)+(part?','+part:'')+' ₫'; }
  const readMoney=input=>number(input.value.replaceAll('.','').replace(',','.'));
  window.dcSetMoneyValue=(input,value)=>{input.value=money(number(value)).replace(' ₫','');};
  function formatInput(input) {
    const before=input.value.slice(0,input.selectionStart??input.value.length).replaceAll('.','').length;
    const raw=input.value.replace(/[^\d,]/g,''), parts=raw.split(',');
    input.value=parts[0] ? fmt.format(BigInt(parts[0]))+(parts.length>1?','+parts[1].slice(0,3):'') : '';
    let seen=0,caret=0; for(;caret<input.value.length&&seen<before;caret++) if(input.value[caret]!=='.') seen++;
    input.setSelectionRange(caret,caret);
  }
  function weight(n) { const rounded=(n+50000n)/100000n;return `${rounded/1000n},${String(rounded%1000n).padStart(3,'0')}`; }
  const readWeight=input=>number(input.dataset.weightExact!==undefined && input.value===input.dataset.weightDisplay ? input.dataset.weightExact : input.value);
  window.dcSetWeightValue=(input,value)=>{input.dataset.weightExact=String(value);input.value=weight(number(value));input.dataset.weightDisplay=input.value;};
  function rates() { return JSON.parse(document.getElementById('dc-editor-rates')?.textContent || '{}'); }
  function recalculate() {
    const f=form(); if (!f?.classList.contains('dc-editor')) return;
    const prices=rates(), groups=new Map(); let total=0n, tasks=0n, unknown=false, count=0,quantity=0n;
    document.querySelectorAll('#dc-lines .dc-line').forEach(row=>{
      if (field(row,'DELETE').checked) {
        row.querySelectorAll('input,select,textarea').forEach(input=>{if(input!==field(row,'DELETE')) input.disabled=true;});
        return;
      }
      count++;
      const code=field(row,'GoldCode').value, meta=prices[code], q=number(field(row,'SL').value)/SCALE;
      quantity+=q;
      row.querySelector('[data-dc-quantity]').textContent=q>1n?'× '+q+' món':'';
      const gold=readWeight(field(row,'GoldWeight')), stone=number(field(row,'DiamondWeight').value);
      field(row,'GoldWeight').setCustomValidity(field(row,'Mode').value==='new' && gold<=0n ? 'Nhập TL vàng lớn hơn 0 cho hàng đặt.' : '');
      field(row,'ProductCode').setCustomValidity(field(row,'Mode').value==='stock' && !field(row,'ProductCode').value.trim() ? 'Quét hoặc nhập mã sản phẩm có sẵn.' : '');
      field(row,'TotalWeight').value=decimal(gold+stone);
      row.querySelectorAll('[data-dc-unit]').forEach(el=>el.textContent=meta?.unit || 'chỉ');
      const w=gold*q, task=readMoney(field(row,'TaskPrice'))*q, rate=number(meta?.rate);
      const known=!!meta && (rate>0n || w===0n), quantum=BigInt(meta?.round_unit||'1');
      const rounded=(n)=>(n+SCALE*SCALE*quantum/2n)/(SCALE*SCALE*quantum)*quantum*SCALE;
      const amount=known ? rounded(w*rate) : 0n, subtotal=known ? rounded(w*rate+task*SCALE) : 0n;
      row.querySelector('[data-dc-row-total]').textContent=known ? money(subtotal) : 'Chưa có giá';
      row.querySelector('[data-dc-row-total]').classList.toggle('dc-price-missing',!known);
      const group=groups.get(code)||{w:0n,amount:0n,known:true,meta}; group.w+=w; group.amount+=amount; group.known&&=known; groups.set(code,group);
      total+=subtotal; tasks+=known?subtotal-amount:task; unknown ||= !known;
    });
    document.getElementById('dc-row-count').textContent=`· ${count} dòng`;
    const root=document.getElementById('dc-gold-totals'); root.replaceChildren();
    groups.forEach((g,code)=>{
      const row=document.createElement('div'); row.className='dc-gold-summary';
      const detail=document.createElement('span'), title=document.createElement('b'), formula=document.createElement('small'), amount=document.createElement('b');
      title.textContent=code||'Chưa chọn loại'; formula.textContent=`${weight(g.w)} ${g.meta?.unit||'chỉ'} × ${g.meta?.rate ? money(number(g.meta.rate)) : 'chưa có giá'}`;
      amount.textContent=g.known ? money(g.amount) : 'Chưa có giá'; detail.append(title,formula); row.append(detail,amount); root.append(row);
    });
    if (!count) root.textContent='Bấm + Hàng sẵn hoặc + Hàng đặt để thêm món.';
    document.getElementById('dc-task-total').textContent=money(tasks);
    document.getElementById('dc-estimate-total').textContent=unknown ? 'Chưa đủ giá' : money(total);
    const cash=f.elements.CashDeposit, bank=f.elements.BankDeposit, deposit=readMoney(cash)+readMoney(bank);
    cash.required=true; bank.required=true;
    bank.setCustomValidity(deposit<=100000n*SCALE ? 'Tổng tiền mặt + chuyển khoản phải lớn hơn 100.000₫.' : '');
    document.getElementById('dc-customer-search').setCustomValidity(f.elements.CustID.value.trim() ? '' : 'Chọn khách hàng từ danh sách trước khi lưu.');
    const statusCount=document.getElementById('dc-status-count'),statusDeposit=document.getElementById('dc-status-deposit');
    if(statusCount) statusCount.textContent=String(quantity);
    if(statusDeposit) statusDeposit.textContent=money(deposit);
    f.elements.TienCoc.value=cash.value!=='' && bank.value!=='' ? money(deposit).replace(' ₫','') : '';
    const note=document.getElementById('dc-deposit-summary');
    note.textContent=cash.value===''||bank.value==='' ? 'Nhập rõ cọc tiền mặt và chuyển khoản; nhập 0 nếu không có.' : deposit<=100000n*SCALE ? 'Tổng tiền mặt + chuyển khoản phải lớn hơn 100.000₫.' : unknown ? 'Bổ sung loại / giá vàng để đối chiếu tổng cọc.' : deposit>total ? 'Cọc vượt tạm tính '+money(deposit-total) : 'Còn lại dự kiến '+money(total-deposit);
    note.classList.toggle('dc-price-missing',unknown||deposit>total||deposit<=100000n*SCALE);
  }
  function hideCustomers() { const r=document.getElementById('dc-customer-results'); if (r) r.hidden=true; document.getElementById('dc-customer-search')?.setAttribute('aria-expanded','false'); }
  function selectCustomer(c) {
    if (!form()) return;
    const same=form().elements.CustID.value===c.CustID;
    form().elements.CustID.value=c.CustID;
    document.getElementById('dc-customer-search').value=c.CustName||c.CustID;
    const choices=document.getElementById('dc-phone-options');if(choices){choices.replaceChildren();for(const value of new Set([c.Phone,c.GhiChu2,c.GhiChu3].filter(Boolean))){const option=document.createElement('option');option.value=value;choices.append(option);}}
    if(form().elements.DocumentPhone && (c.selected_phone!==undefined || !same || !form().elements.DocumentPhone.value))form().elements.DocumentPhone.value=c.selected_phone??c.Phone??'';
    document.getElementById('dc-customer-contact').textContent=`${form().elements.DocumentPhone?.value||c.Phone||'Chưa có SĐT'} · CCCD ${c.CMND||'—'} · ${c.CustID}`;
    document.getElementById('dc-customer-address').textContent=c.Address||'Chưa có địa chỉ'; hideCustomers();
    document.getElementById('dc-customer-search').setCustomValidity('');
  }
  let customerTimer, controller;
  async function findCustomer(input) {
    clearTimeout(customerTimer); controller?.abort(); hideCustomers();
    form().elements.CustID.value=''; document.getElementById('dc-customer-contact').textContent='Chọn khách từ danh sách kết quả'; document.getElementById('dc-customer-address').textContent='';
    input.setCustomValidity('Chọn khách hàng từ danh sách trước khi lưu.');
    const query=input.value.trim(); if(query.length<2) return;
    controller=new AbortController(); const signal=controller.signal;
    customerTimer=setTimeout(async()=>{
      const result=document.getElementById('dc-customer-results'); if (!result) return;
      result.hidden=false; input.setAttribute('aria-expanded','true'); result.textContent='Đang tìm khách…';
      try {
        const url=new URL(form().dataset.customersUrl,location.origin); url.searchParams.set('customer_q',query); url.searchParams.set('format','json');
        const response=await fetch(url,{signal}); if (!response.ok) throw Error('Không tải được khách. Vui lòng thử lại.');
        const data=await response.json(); if (!input.isConnected||input.value.trim()!==query) return;
        result.replaceChildren();
        if (!data.rows.length) result.textContent='Chưa tìm thấy khách. Bấm + để thêm khách hàng.';
        data.rows.forEach(c=>{
          const b=document.createElement('button'); b.type='button'; b.className='dc-customer-option'; b.setAttribute('role','option');
          const title=document.createElement('b'),detail=document.createElement('small'); title.textContent=c.CustName||c.CustID; detail.textContent=`${c.selected_phone||c.Phone||'—'} · ${c.Address||c.CMND||c.CustID}`;
          b.append(title,detail); b.addEventListener('click',()=>selectCustomer(c)); result.append(b);
        });
      } catch(e) { if(e.name!=='AbortError') result.textContent=e.message; }
    },250);
  }
  const closeOriginal=window.closeKhblModal;
  window.closeKhblModal=function() {
    if(cameraDialog) {closeCamera();return;}
    const child=document.getElementById('dc-customer-modal');
    if (child) {
      window.__khblStopCamera?.(); child.remove();
      const parent=form()?.closest('.khbl-modal'); if (parent) { parent.inert=false; parent.removeAttribute('aria-hidden'); }
      document.getElementById('dc-customer-search')?.focus(); return;
    }
    closeOriginal();
  };
  async function addCustomer() {
    const f=form(); if (!f||document.getElementById('dc-customer-modal')) return;
    const query=document.getElementById('dc-customer-search').value.trim(); hideCustomers();
    const child=document.createElement('div'); child.id='dc-customer-modal'; child.dataset.khblLopCon='';
    const parent=f.closest('.khbl-modal'); parent.inert=true; parent.setAttribute('aria-hidden','true');
    document.getElementById('modal-root').append(child);
    try {
      const url=new URL(f.dataset.customerAddUrl,location.origin); url.searchParams.set('q',query);
      await htmx.ajax('GET',url.href,{target:child,swap:'innerHTML'});
      if (!child.querySelector('#kh-form')) throw Error('Không mở được popup khách hàng.');
      window.khblBind?.(child);
      if (query.includes('|')) { child.querySelector('#kh-qr-in').value=query; child.querySelector('#kh-qr-btn').click(); }
      else if (/^\d{12}$/.test(query)) child.querySelector('#f-cccd').value=query;
      else if (query && !/^[+\d\s().-]+$/.test(query)) child.querySelector('#f-ten').value=query;
      child.querySelector('#f-ten')?.focus();
    } catch(e) { window.closeKhblModal(); document.getElementById('dc-customer-contact').textContent='Không mở được popup khách. Kiểm tra kết nối và thử lại.'; }
  }
  window.dcEditorRecalculate=recalculate;
  window.dcEditorBind=()=>{
    const f=form(); if (!f?.classList.contains('dc-editor')) return;
    if(!f.dataset.notesBound) {f.dataset.notesBound='1';renderNotes();f.addEventListener('submit',()=>{if(f.elements.NoteText?.value.trim()) addNote();},true);}
    const situation=f.querySelector('.dc-situation');if(situation) situation.dataset.progress=f.elements.Progress.value;
    if (!f.dataset.customerBound) {
      f.dataset.customerBound='1';
      const initial=JSON.parse(document.getElementById('dc-customer-initial').textContent||'{}');
      if (initial.CustID===f.elements.CustID.value) selectCustomer(initial);
      else if (f.elements.CustID.value) loadSelected(f.elements.CustID.value);
    }
    recalculate();
  };
  async function loadSelected(id) {
    const f=form(); if (!f) return;
    try {
      const url=new URL(f.dataset.customersUrl,location.origin); url.searchParams.set('cust_id',id); url.searchParams.set('format','json');
      const response=await fetch(url); if (!response.ok) throw Error(); const data=await response.json();
      if (form()===f && data.rows[0]) selectCustomer(data.rows[0]);
    } catch(_) { if (form()===f) document.getElementById('dc-customer-contact').textContent='Khách '+id+' đã lưu; chưa tải được chi tiết.'; }
  }
  document.addEventListener('khachSaved',event=>{ if (form()?.classList.contains('dc-editor') && event.detail?.custId) loadSelected(event.detail.custId); });
  document.addEventListener('input',event=>{
    if(event.target.matches('#dc-save [data-dc-weight]')) delete event.target.dataset.weightExact;
    if(event.target.matches('#dc-save [data-dc-money]')) formatInput(event.target);
    if (event.target.id==='dc-customer-search') findCustomer(event.target);
    else if (event.target.closest('#dc-save')) recalculate();
  });
  document.addEventListener('focusout',event=>{
    const input=event.target;
    if(input.matches('#dc-save [data-dc-weight]') && /^\d+(?:[.,]\d+)?$/.test(input.value)) {
      if(input.dataset.weightExact===undefined || input.value!==input.dataset.weightDisplay) window.dcSetWeightValue(input,input.value.replace(',','.'));
      recalculate();
    }
  });
  document.addEventListener('htmx:configRequest',event=>{
    if(event.detail.elt!==form()) return;
    form().querySelectorAll('[data-dc-weight]').forEach(input=>{
      if(!input.matches(':disabled') && input.value===input.dataset.weightDisplay && input.dataset.weightExact!==undefined)
        event.detail.parameters[input.name]=input.dataset.weightExact;
    });
  });
  document.addEventListener('change',event=>{
    if(event.target.name==='Progress') {
      const value=event.target.value,root=document.querySelector('.dc-situation');root.dataset.progress=value;
      document.getElementById('dc-progress-hint').textContent=value.startsWith('cancel_')?'Bắt buộc thêm ghi chú mới nêu lý do hủy.':value==='applied'?'Tự cập nhật theo hóa đơn đã chốt.':value==='delivered'?'Kết thúc theo dõi đặt hàng; vẫn đủ điều kiện áp dụng cọc.':value==='ready'?'Đã chuẩn bị xong; đủ điều kiện áp dụng cọc.':'';
    }
    if(event.target.matches('[data-dc-photo-input]')) {
      const input=event.target, file=input.files[0], card=input.closest('.dc-photo'); if(!file) return;
      if(file.size>10*1024*1024) {input.value=''; window.dcToast?.('Mỗi ảnh tối đa 10 MB.','error'); return;}
      const image=card.querySelector('img'); if(image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
      image.dataset.objectUrl=URL.createObjectURL(file); image.src=image.dataset.objectUrl; image.hidden=false;
      card.querySelector('.dc-photo-preview span').hidden=true; card.querySelector('[name^=remove_photo_]').value='0';
    }
    if(event.target.closest('#dc-save')) recalculate();
  });
  document.addEventListener('click',event=>{
    if(event.target.closest('[data-dc-unlock]')) unlockEditor();
    if(event.target.closest('[data-dc-note-add]')) addNote();
    const promiseToggle=event.target.closest('[data-dc-promise-toggle]');
    if(promiseToggle) {const menu=document.querySelector('.dc-promise-menu');menu.hidden=!menu.hidden;menu.dataset.khblLopCon='';promiseToggle.setAttribute('aria-expanded',String(!menu.hidden));}
    const days=event.target.closest('[data-dc-promise-days]');
    if(days) {const date=new Date(document.getElementById('dc-today').value+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+Number(days.dataset.dcPromiseDays));form().elements.PromiseDate.value=date.toISOString().slice(0,10);hidePromise();}
    if(!event.target.closest('.dc-promise-input')) hidePromise();
    if(event.target.matches('[data-dc-photo-preview]') && !event.target.hidden) {
      const dialog=document.createElement('dialog');dialog.className='dc-photo-zoom';dialog.dataset.khblLopCon='';
      const img=document.createElement('img');img.src=event.target.src;img.alt=event.target.alt;
      const close=document.createElement('button');close.type='button';close.textContent='Đóng';close.onclick=()=>dialog.close();
      dialog.append(img,close);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
    }
    if (event.target.closest('[data-dc-customer-add]')) addCustomer();
    const pick=event.target.closest('[data-dc-photo-pick]');
    if(pick) {const input=pick.closest('.dc-photo').querySelector('[type=file]'); if(pick.dataset.dcPhotoPick==='camera') openCamera(input); else {input.removeAttribute('capture'); input.click();}}
    const removePhoto=event.target.closest('[data-dc-photo-remove]');
    if(removePhoto) {const card=removePhoto.closest('.dc-photo');card.querySelector('[type=file]').value='';card.querySelector('[name^=remove_photo_]').value='1';card.querySelector('img').hidden=true;card.querySelector('.dc-photo-preview span').hidden=false;}
    const remove=event.target.closest('[data-dc-remove-line]');
    if (remove) { const row=remove.closest('.dc-line'); field(row,'DELETE').checked=true; row.hidden=true; recalculate(); }
    if (!event.target.closest('.dc-customer-picker')) hideCustomers();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Enter' && event.target.name==='NoteText') {event.preventDefault();addNote();return;}
    if(event.key==='Escape' && document.querySelector('.dc-promise-menu:not([hidden])')) {hidePromise();return;}
    if (event.key==='Escape' && document.getElementById('dc-customer-modal') && !document.querySelector('#kh-cat-root .khbl-modal') && !document.querySelector('#dc-customer-modal [data-khbl-lop-con]:not([hidden])')) {event.stopPropagation(); window.closeKhblModal(); return;}
    const search=document.getElementById('dc-customer-search'), result=document.getElementById('dc-customer-results');
    if (!search||!result) return;
    if(event.target===search && event.key==='Enter') {event.preventDefault(); if(!result.hidden) result.querySelector('button')?.click();}
    if((event.target===search||event.target.closest('#dc-customer-results')) && ['ArrowDown','ArrowUp','Escape'].includes(event.key)) {
      event.preventDefault(); if(event.key==='Escape') {hideCustomers(); search.focus(); return;}
      const options=[...result.querySelectorAll('button')], index=options.indexOf(event.target), step=event.key==='ArrowDown'?1:-1;
      if(options.length) options[(index+step+options.length)%options.length].focus();
    }
  });
  document.addEventListener('htmx:afterSwap',window.dcEditorBind);
  document.addEventListener('DOMContentLoaded',window.dcEditorBind);
})();
