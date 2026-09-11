/* Popup đặt cọc: tính tiền số nguyên cố định, khách hàng và popup khách lồng. */
(() => {
  'use strict';
  const SCALE=100000000n, fmt=new Intl.NumberFormat('vi-VN');
  const field=(row,key)=>row.querySelector(`[name$="-${key}"]`);
  const form=()=>document.getElementById('dc-save');
  function number(value) {
    const m=String(value ?? '').trim().match(/^(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
    if (!m) return 0n;
    const exponent=Number(m[3]||0), fraction=m[2]||'', shift=8+exponent-fraction.length;
    if (Math.abs(shift)>40) return 0n;
    const n=BigInt(m[1]+fraction); return shift>=0 ? n*10n**BigInt(shift) : n/10n**BigInt(-shift);
  }
  function decimal(n) { return `${n/SCALE}.${String(n%SCALE).padStart(8,'0')}`.replace(/\.?0+$/,'') || '0'; }
  function money(n) { const whole=n/SCALE, part=String(n%SCALE).padStart(8,'0').replace(/0+$/,''); return fmt.format(whole)+(part?','+part:'')+' ₫'; }
  function weight(n) { return decimal(n).replace('.',','); }
  function rates() { return JSON.parse(document.getElementById('dc-editor-rates')?.textContent || '{}'); }
  function recalculate() {
    const f=form(); if (!f?.classList.contains('dc-editor')) return;
    const prices=rates(), groups=new Map(); let total=0n, tasks=0n, unknown=false, count=0;
    document.querySelectorAll('#dc-lines .dc-line').forEach(row=>{
      if (field(row,'DELETE').checked) return;
      count++;
      const code=field(row,'GoldCode').value, meta=prices[code], q=number(field(row,'SL').value)/SCALE;
      const gold=number(field(row,'GoldWeight').value), stone=number(field(row,'DiamondWeight').value);
      field(row,'TotalWeight').value=decimal(gold+stone);
      row.querySelectorAll('[data-dc-unit]').forEach(el=>el.textContent=meta?.unit || 'chỉ');
      const w=gold*q, task=number(field(row,'TaskPrice').value)*q, rate=number(meta?.rate);
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
    const cash=f.elements.CashDeposit, bank=f.elements.BankDeposit, deposit=number(cash.value)+number(bank.value);
    f.elements.TienCoc.value=cash.value!=='' && bank.value!=='' ? decimal(deposit) : '';
    const note=document.getElementById('dc-deposit-summary');
    note.textContent=cash.value===''||bank.value==='' ? 'Nhập rõ cọc tiền mặt và chuyển khoản; nhập 0 nếu không có.' : unknown ? 'Bổ sung loại / giá vàng để đối chiếu tổng cọc.' : deposit>total ? 'Cọc vượt tạm tính '+money(deposit-total) : 'Còn lại dự kiến '+money(total-deposit);
    note.classList.toggle('dc-price-missing',unknown||deposit>total);
  }
  function hideCustomers() { const r=document.getElementById('dc-customer-results'); if (r) r.hidden=true; document.getElementById('dc-customer-search')?.setAttribute('aria-expanded','false'); }
  function selectCustomer(c) {
    if (!form()) return;
    form().elements.CustID.value=c.CustID;
    document.getElementById('dc-customer-search').value=c.CustName||c.CustID;
    document.getElementById('dc-customer-contact').textContent=`${c.Phone||'Chưa có SĐT'} · CCCD ${c.CMND||'—'} · ${c.CustID}`;
    document.getElementById('dc-customer-address').textContent=c.Address||'Chưa có địa chỉ'; hideCustomers();
  }
  let customerTimer, controller;
  async function findCustomer(input) {
    clearTimeout(customerTimer); controller?.abort(); hideCustomers();
    form().elements.CustID.value=''; document.getElementById('dc-customer-contact').textContent='Chọn khách từ danh sách kết quả'; document.getElementById('dc-customer-address').textContent='';
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
          const title=document.createElement('b'),detail=document.createElement('small'); title.textContent=c.CustName||c.CustID; detail.textContent=`${c.Phone||'—'} · ${c.CMND||c.CustID}`;
          b.append(title,detail); b.addEventListener('click',()=>selectCustomer(c)); result.append(b);
        });
      } catch(e) { if(e.name!=='AbortError') result.textContent=e.message; }
    },250);
  }
  const closeOriginal=window.closeKhblModal;
  window.closeKhblModal=function() {
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
    if (event.target.id==='dc-customer-search') findCustomer(event.target);
    else if (event.target.closest('#dc-save')) recalculate();
  });
  document.addEventListener('change',event=>{if(event.target.closest('#dc-save')) recalculate();});
  document.addEventListener('click',event=>{
    if (event.target.closest('[data-dc-customer-add]')) addCustomer();
    const remove=event.target.closest('[data-dc-remove-line]');
    if (remove) { const row=remove.closest('.dc-line'); field(row,'DELETE').checked=true; row.hidden=true; recalculate(); }
    if (!event.target.closest('.dc-customer-picker')) hideCustomers();
  });
  document.addEventListener('keydown',event=>{
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
