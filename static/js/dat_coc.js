/* Phiếu đặt hàng: tab trạng thái, popup và tra cứu hàng tồn. */
(() => {
  'use strict';
  function toast(message, tone = 'success') {
    const root = document.getElementById('toast-root');
    if (!root) return;
    const item = document.createElement('div');
    item.className = tone;
    item.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    item.textContent = message; root.append(item);
    item.addEventListener('click', () => item.remove());
    setTimeout(() => item.remove(), tone === 'error' ? 12000 : 5000);
  }
  function filter() { document.getElementById('dc-filter')?.requestSubmit(); }
  window.dcToast=toast;
  function field(line, key) { return line.querySelector(`[name$="-${key}"]`); }
  function mode(line) {
    const stock = field(line, 'Mode')?.value === 'stock';
    line.classList.toggle('is-stock', stock);
    ['ProductDesc','GoldWeight','Size','TaskPrice'].forEach(key=>{field(line,key).readOnly=stock;});
    field(line,'GoldCode').disabled=stock;
    field(line,'ProductCode').readOnly=!stock;
    if (!stock) field(line,'ProductCode').value='Khách đặt';
    field(line,'ProductCode').placeholder='Quét / nhập mã SP';
    if (field(line, 'DELETE')?.checked) line.hidden = true;
    if (!stock) line.querySelector('.dc-stock-results').replaceChildren();
  }
  function bindLines() { document.querySelectorAll('#dc-lines .dc-line').forEach(mode); window.dcEditorBind?.(); }
  document.addEventListener('click', event => {
    const form = document.getElementById('dc-filter');
    const tab = event.target.closest('[data-dc-tab]');
    if (tab && (!form || tab.dataset.dcTab === 'notifications')) {
      htmx.ajax('GET', '/banle/dat-coc/?tab=' + encodeURIComponent(tab.dataset.dcTab), {target:'#dc-content'}); return;
    }
    const messagePage = event.target.closest('[data-dc-message-page]');
    if (messagePage) { const mf=document.getElementById('dc-message-filter'); mf.elements.page.value=messagePage.dataset.dcMessagePage; mf.requestSubmit(); }
    const copy = event.target.closest('[data-dc-copy]');
    if (copy) navigator.clipboard.writeText(copy.dataset.dcCopy).then(()=>toast('Đã chép nội dung tin.')).catch(()=>toast('Không chép được. Hãy chọn và sao chép nội dung tin.','error'));
    if (tab && form) {
      if (form.elements.tab.value === tab.dataset.dcTab) return;
      form.elements.tab.value = tab.dataset.dcTab;
      form.elements.due.value = ''; form.elements.quick.value = '';
      form.elements.sort.value = ['ready','ordering'].includes(tab.dataset.dcTab) ? 'priority' : 'newest';
      form.elements.page.value = '1'; filter();
    }
    const work = event.target.closest('[data-dc-work]');
    if (work && form) {
      const key = work.dataset.dcWork;
      form.elements.tab.value = 'all';
      form.elements.quick.value = key === 'pending' ? '' : key;
      form.elements.due.value = ''; form.elements.sort.value = 'priority'; form.elements.page.value = '1'; filter();
    }
    if (event.target.closest('[data-dc-refresh]') && form) {
      const flag = document.createElement('input'); flag.type = 'hidden'; flag.name = 'refresh'; flag.value = '1';
      form.append(flag); filter(); flag.remove();
    }
    const page = event.target.closest('[data-dc-page]');
    if (page && form) { form.elements.page.value = page.dataset.dcPage; filter(); }
    if (event.target.closest('[data-dc-retry]')) filter();
    if (event.target.closest('[data-dc-reset]') && form) {
      ['q', 'start', 'end', 'due', 'quick', 'source'].forEach(name => { form.elements[name].value = ''; });
      form.elements.mine.checked = false;
      form.elements.sort.value = 'newest'; form.elements.page.value = '1'; filter();
    }
    if (event.target.closest('[data-dc-print]')) window.print();
    const customer = event.target.closest('[data-dc-customer]');
    if (customer) {
      document.querySelector('#dc-save [name=CustID]').value = customer.dataset.dcCustomer;
      document.getElementById('dc-customer-results').textContent = 'Đã chọn: ' + customer.dataset.name;
    }
    const add = event.target.closest('[data-dc-add-line]');
    if (add) {
      const total = document.getElementById('id_items-TOTAL_FORMS');
      const n = Number(total.value);
      if (n >= 50) { toast('Mỗi phiếu tối đa 50 món.', 'warning'); return; }
      const template = document.getElementById('dc-empty-line');
      document.getElementById('dc-lines').insertAdjacentHTML('beforeend', template.innerHTML.replaceAll('__prefix__', String(n)));
      total.value = n + 1;
      const line = document.querySelector('#dc-lines .dc-line:last-child');
      field(line, 'Mode').value = add.dataset.dcAddLine || 'new'; mode(line);
      field(line, add.dataset.dcAddLine === 'stock' ? 'ProductCode' : 'ProductDesc').focus();
      window.dcEditorRecalculate?.();
    }
  });
  document.addEventListener('change', event => {
    if(event.target.closest('#dc-filter') && event.target.name!=='page') document.getElementById('dc-filter').elements.page.value='1';
    const selection=document.getElementById('dc-reminder-selection');
    if (selection && event.target.closest('#dc-reminder-selection')) {
      const boxes=[...selection.querySelectorAll('input[name=orders]')];
      if(event.target.matches('[data-dc-select-all]')) boxes.forEach(box=>box.checked=event.target.checked);
      const count=boxes.filter(box=>box.checked).length, all=selection.querySelector('[data-dc-select-all]');
      if(all) {all.checked=count>0 && count===boxes.length; all.indeterminate=count>0 && count<boxes.length;}
      selection.querySelector('[data-dc-selected-count]').textContent=`${count} phiếu đã chọn`;
      selection.querySelector('[data-dc-bulk-compose]').disabled=count===0 || count>200;
      if(count>200) toast('Mỗi lượt tối đa 200 phiếu.','warning');
    }
    if (event.target.closest('#dc-message-filter') && event.target.name !== 'page') document.getElementById('dc-message-filter').elements.page.value = '1';
    if (event.target.matches('#dc-lines [name$="-Mode"]')) mode(event.target.closest('.dc-line'));
  });
  const searches = new WeakMap();
  document.addEventListener('input', event => {
    if (!event.target.matches('#dc-lines [name$="-ProductCode"]')) return;
    const input = event.target, line = input.closest('.dc-line'), results = line.querySelector('.dc-stock-results');
    const previous = searches.get(input);
    if (previous) { clearTimeout(previous.timer); previous.controller.abort(); }
    const query = input.value.trim(), controller = new AbortController();
    results.replaceChildren();
    if (field(line,'Mode').value==='stock') {
      ['ProductDesc','GoldCode','GoldWeight','DiamondWeight','TotalWeight','Size','TaskPrice'].forEach(key=>{field(line,key).value='';});
      line.classList.remove('is-verified'); window.dcEditorRecalculate?.();
    }
    if (query.length < 2 || field(line,'Mode').value !== 'stock') return;
    const timer = setTimeout(async () => {
      results.textContent = 'Đang tìm hàng trong kho…';
      try {
        const form = document.getElementById('dc-save');
        const url = new URL(form.dataset.productsUrl, location.origin);
        url.searchParams.set('q', query); url.searchParams.set('units', form.dataset.units); url.searchParams.set('exact','1');
        const response = await fetch(url, {signal:controller.signal, headers:{'Accept':'application/json'}});
        if (!response.ok) throw new Error('Không tải được hàng trong kho. Vui lòng thử lại.');
        const data = await response.json();
        if (!input.isConnected || input.value.trim() !== query || field(line,'Mode').value !== 'stock') return;
        results.replaceChildren();
        if (data.rows.length!==1) {results.textContent='Mã chưa đúng hoặc hàng không còn sẵn. Quét/nhập lại mã.'; return;}
        const row=data.rows[0];
        ['ProductCode','ProductDesc','GoldCode','TotalWeight','DiamondWeight','GoldWeight'].forEach(key=>{field(line,key).value=row[key]??'';});
        window.dcSetWeightValue?.(field(line,'GoldWeight'),row.GoldWeight||'0');
        field(line,'Size').value=row.RingSize||'';
        window.dcSetMoneyValue?.(field(line,'TaskPrice'),row.TaskPrice||'0');
        line.classList.add('is-verified');
        window.dcEditorRecalculate?.();
      } catch (error) { if (error.name !== 'AbortError' && input.isConnected) results.textContent = error.message; }
    }, 300);
    searches.set(input, {timer,controller});
  });
  document.addEventListener('keydown', event => {
    if (event.key==='Enter' && event.target.matches('#dc-lines [name$="-ProductCode"]')) {
      event.preventDefault(); event.target.dispatchEvent(new Event('input',{bubbles:true})); return;
    }
    const tab = event.target.closest('[data-dc-tab]');
    if (tab && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[data-dc-tab]')], index = tabs.indexOf(tab);
      const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      next.focus(); next.click();
    }
  });
  document.body.addEventListener('depositSaved', event => {
    window.closeKhblModal(); toast(event.detail.message,event.detail.tone || 'success'); htmx.trigger(document.body, 'depositRefresh');
    if(event.detail.open_url) htmx.ajax('GET',event.detail.open_url,{target:'#modal-root'});
  });
  document.body.addEventListener('htmx:afterSwap', event => {
    if (event.detail.target.id === 'modal-root') {
      document.querySelector('#modal-root .dc-popup')?.classList.add('dc-purple');
      bindLines();
      const error = document.querySelector('[data-dc-error]');
      if (error) toast(error.textContent, 'error');
    }
  });
  ['htmx:responseError','htmx:sendError'].forEach(name => document.body.addEventListener(name, () => toast('Không hoàn tất yêu cầu. Kiểm tra kết nối / quyền truy cập rồi tải lại danh sách.', 'error')));
})();
