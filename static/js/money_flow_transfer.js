(function () {
  const f = document.getElementById('mf-payment-form');
  const digits = ['không','một','hai','ba','bốn','năm','sáu','bảy','tám','chín'];
  function triplet(n, full) {
    const h = Math.floor(n/100), t = Math.floor(n/10)%10, u=n%10;
    let s = h || full ? digits[h]+' trăm' : '';
    if(t>1) s+=' '+digits[t]+' mươi'; else if(t===1) s+=' mười'; else if(u && (h || full)) s+=' lẻ';
    if(u) s+=' '+(u===1 && t>1?'mốt':u===5 && t>0?'lăm':digits[u]);
    return s.trim();
  }
  function words(n) {
    if(!Number.isSafeInteger(n) || n<0) return 'Số tiền không hợp lệ';
    if(n===0) return 'Không đồng';
    const units=['','nghìn','triệu','tỷ','nghìn tỷ','triệu tỷ'], groups=[];
    while(n>0){groups.push(n%1000); n=Math.floor(n/1000);}
    const out=[];
    for(let i=groups.length-1;i>=0;i--) if(groups[i]) out.push(triplet(groups[i], i<groups.length-1)+' '+units[i]);
    const s=out.join(' ').trim()+' đồng'; return s.charAt(0).toUpperCase()+s.slice(1);
  }
  const qr = document.querySelector('#modal-root [data-qr-amount]');
  if(qr) qr.querySelector('[data-amount-words]').textContent=words(Number(qr.dataset.qrAmount));
  const pollForm = document.querySelector('#modal-root [data-pawn-poll]');
  if(pollForm && !pollForm.dataset.started) {
    pollForm.dataset.started='1';
    const panel=pollForm.closest('.transfer-panel'), status=panel.querySelector('.transfer-processing');
    let activeController=null,timer=null,stopped=false,busy=false,failures=0;
    function stop(){stopped=true;clearTimeout(timer);activeController?.abort();observer.disconnect();document.removeEventListener?.('visibilitychange',resume);}
    function resume(){if(!document.hidden && !stopped){clearTimeout(timer);poll();}else{clearTimeout(timer);activeController?.abort();}}
    const observer=new MutationObserver(()=>{
      if(!pollForm.isConnected)stop();
    });
    observer.observe(document.getElementById('modal-root'),{childList:true,subtree:true});
    async function poll() {
      if(!pollForm.isConnected || stopped || busy || document.hidden) return;
      busy=true;
      activeController=new AbortController();
      const timeout=setTimeout(()=>activeController.abort(),15000);
      try {
        const response=await fetch(pollForm.dataset.pawnPoll, {method:'POST', credentials:'same-origin',
          body:new FormData(pollForm), headers:{'X-Requested-With':'XMLHttpRequest',...(location.pathname.startsWith('/banle/mobile/')?{'X-KHBL-Mobile':'1'}:{})}, signal:activeController.signal});
        if(!pollForm.isConnected) return;
        if(response.status===403) {status.textContent='Phiên đăng nhập hoặc quyền đã thay đổi. Vui lòng mở lại trang.';stop();return;}
        if(response.status>=500)throw Error('Server unavailable');
        const data=await response.json();
        failures=0;
        if(data.status==='success') {
          status.textContent='THÀNH CÔNG · '+Number(data.received).toLocaleString('vi-VN')+' đ đã được xác minh';
          const frame=panel.querySelector('.transfer-qr-frame');
          frame.replaceChildren(); const ok=document.createElement('strong');
          ok.textContent='✓ ĐÃ NHẬN TIỀN';ok.className='transfer-success-mark';frame.classList?.add('is-success');frame.append(ok);
          const detail=document.createElement('p');detail.className='transfer-success-detail';detail.textContent='Đã xác minh khoản chuyển của mã QR này';frame.append(detail);
          const download=panel.closest('.khbl-modal')?.querySelector('[download]');if(download) download.hidden=true;
          document.dispatchEvent(new CustomEvent('money-flow-reconciled'));
          stop();
          return;
        }
        if(data.status==='review') {status.textContent='Cần kiểm tra · '+data.message;stop();return;}
        status.querySelector('small').textContent=data.status==='pending' ? data.message : data.status==='partial'
          ?'Đã nhận '+Number(data.received).toLocaleString('vi-VN')+' đ · chờ phần còn lại'
          :'Đang chờ chứng từ ngân hàng';
      } catch (_) {
        failures++;
        if(pollForm.isConnected) status.querySelector('small').textContent='Chưa kiểm tra được ngân hàng · đang thử lại';
      } finally {clearTimeout(timeout);busy=false;}
      if(pollForm.isConnected && !stopped && !document.hidden) timer=setTimeout(poll,failures?Math.min(15000,1000*2**failures):1000);
    }
    document.addEventListener?.('visibilitychange',resume);
    poll();
  }
  if (!f || f.dataset.initialized) return;
  f.dataset.initialized = '1';
  const e = f.elements;
  let total = Number(f.dataset.total);
  function amount() {
    const n=Number(e.bank_amount.value);
    f.querySelector('[data-amount-display]').textContent=Number.isFinite(n)?n.toLocaleString('vi-VN')+' đ':'—';
    f.querySelector('[data-amount-words]').textContent=words(n);
  }
  const key='khbl.qr.bank.'+f.dataset.bankGroup;
  try { const saved=localStorage.getItem(key); if(f.dataset.keepPost!=='1' && [...e.bank_id.options].some(o=>o.value===saved && o.value)) e.bank_id.value=saved; } catch (_) {}
  function bank(save) {
    const o=e.bank_id.selectedOptions[0], s=f.querySelector('.transfer-bank');
    s.querySelector('b').textContent=o?.dataset.bank||'';
    s.querySelector('strong').textContent=o?.dataset.account||'';
    s.querySelector('span').textContent=o?.dataset.owner||'';
    if(save) try{localStorage.setItem(key,e.bank_id.value);}catch(_){}
  }
  e.bank_id.addEventListener('change',()=>bank(true));
  const moneyInputs=[...f.querySelectorAll('[data-money]')];
  function displayMoney(input, preserveCaret=false) {
    const before=input.value.slice(0,input.selectionStart ?? input.value.length).replace(/\D/g,'').length;
    const raw=e[input.dataset.money].value;
    input.value=raw===''?'':Number(raw).toLocaleString('vi-VN');
    if(preserveCaret) {
      let pos=0,count=0;
      while(pos<input.value.length && count<before) {if(/\d/.test(input.value[pos])) count++;pos++;}
      input.setSelectionRange(pos,pos);
    }
  }
  function validateMoney() {
    moneyInputs.forEach(input=>{
      const raw=e[input.dataset.money].value,n=Number(raw);
      input.setCustomValidity(raw!=='' && Number.isSafeInteger(n) && n>=0 && n<=total &&
        (input.dataset.money!=='bank_amount' || n>0) ? '' : 'Số tiền không hợp lệ hoặc vượt số tiền còn lại.');
    });
  }
  moneyInputs.forEach(input=>{
    input.addEventListener('input',()=>{
      // Dots and spaces are grouping separators; reject signs/letters instead of changing their meaning.
      if(!/^[\d.\s]*$/.test(input.value)) {input.setCustomValidity('Chỉ nhập số tiền nguyên dương.');return;}
      const entered=input.value.replace(/[.\s]/g,'');
      const raw=entered===''?'0':String(Math.min(Number(entered),total));
      e[input.dataset.money].value=raw;
      const other=moneyInputs.find(item=>item!==input),n=Number(raw);
      e[other.dataset.money].value=raw!=='' && Number.isSafeInteger(n) && n<=total ? String(total-n) : '';
      displayMoney(input,true);displayMoney(other);validateMoney();amount();
    });
    displayMoney(input);
  });
  f.querySelectorAll('[data-clear-money]').forEach(button=>button.addEventListener('click',()=>{
    const input=moneyInputs.find(item=>item.dataset.money===button.dataset.clearMoney);
    input.value='0';input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
  }));
  validateMoney();
  bank(false); amount();
  const checkForm=document.querySelector('#modal-root [data-step1-check]');
  if(checkForm && !checkForm.dataset.started){
    checkForm.dataset.started='1';
    const panel=f.closest('.transfer-panel'),refresh=panel.querySelector('[data-step1-refresh]'),message=panel.querySelector('[data-step1-status]');
    const submit=panel.closest('.khbl-modal')?.querySelector('[form="mf-payment-form"]');
    let busy=false,controller=null,authFailed=false;
    const observer=new MutationObserver(()=>{if(!checkForm.isConnected){controller?.abort();observer.disconnect();}});
    observer.observe(document.getElementById('modal-root'),{childList:true,subtree:true});
    async function check(){
      if(busy || !checkForm.isConnected)return;
      busy=true;if(refresh)refresh.disabled=true;if(submit)submit.disabled=true;
      controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);
      try{
        const response=await fetch(checkForm.dataset.step1Check,{method:'POST',credentials:'same-origin',body:new FormData(checkForm),signal:controller.signal});
        authFailed=response.status===401||response.status===403;
        if(!response.ok)throw Error('Không kiểm tra được ngân hàng. Vui lòng thử lại.');
        const data=await response.json();if(!checkForm.isConnected)return;
        if(data.status==='review')throw Error(data.message);
        const remaining=Number(data.remaining);
        if(!Number.isFinite(remaining)||remaining<0)throw Error('Số tiền còn lại không hợp lệ.');
        const changed=remaining!==total;
        total=remaining;f.dataset.total=String(total);
        e.bank_amount.value=String(Math.min(Number(e.bank_amount.value)||0,total));
        e.cash_amount.value=String(total-Number(e.bank_amount.value));
        moneyInputs.forEach(input=>displayMoney(input));validateMoney();amount();
        for(const item of data.history||[]){
          const row=panel.querySelector('[data-history-id="'+item.id+'"]');if(!row)continue;
          row.hidden=!(item.status==='success'||(['ready','superseded'].includes(item.status)&&Number(item.amount)>0&&Number(item.amount)<=total));
          if(item.status==='success'){
            const button=row.querySelector('button');if(button){const icon=document.createElement('span');icon.textContent='✔️';icon.setAttribute('aria-label','Thành công');button.replaceWith(icon);}
          }
        }
        const list=panel.querySelector('.transfer-qr-list');if(list)list.hidden=![...list.querySelectorAll('[data-history-id]')].some(row=>!row.hidden);
        message.hidden=!changed || data.status==='idle';message.textContent=changed?'Đã nhận '+Number(data.received).toLocaleString('vi-VN')+' đ · Còn '+total.toLocaleString('vi-VN')+' đ':'';
        if(changed)document.dispatchEvent(new CustomEvent('money-flow-reconciled'));
        if(submit)submit.disabled=total<=0;
      }catch(error){if(checkForm.isConnected){message.hidden=false;message.textContent=error.message;}}
      finally{clearTimeout(timeout);busy=false;if(refresh)refresh.disabled=false;if(submit && checkForm.isConnected)submit.disabled=authFailed||total<=0;}
    }
    refresh?.addEventListener('click',check);check();
  }
})();
