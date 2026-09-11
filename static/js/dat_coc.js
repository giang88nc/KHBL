/* ĐẶT-CỌC: popup HTMX, thao tác bất đồng bộ và thông báo không chặn màn hình. */
(() => {
  'use strict';
  function toast(message, tone = 'success') {
    const root = document.getElementById('toast-root');
    if (!root) return;
    const item = document.createElement('div');
    item.className = tone;
    item.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    item.textContent = message;
    root.append(item);
    item.addEventListener('click', () => item.remove());
    setTimeout(() => item.remove(), tone === 'error' ? 12000 : 5000);
  }
  function filter() {
    const form = document.getElementById('dc-filter');
    if (form) form.requestSubmit();
  }
  document.addEventListener('click', event => {
    const tab = event.target.closest('[data-dc-tab]');
    const page = event.target.closest('[data-dc-page]');
    const form = document.getElementById('dc-filter');
    if (tab && form) {
      form.elements.tab.value = tab.dataset.dcTab;
      form.elements.page.value = '1'; filter();
    }
    if (page && form) { form.elements.page.value = page.dataset.dcPage; filter(); }
    if (event.target.closest('[data-dc-retry]')) filter();
    if (event.target.closest('[data-dc-reset]') && form) {
      ['q', 'start', 'end', 'status'].forEach(name => { form.elements[name].value = ''; });
      form.elements.page.value = '1'; filter();
    }
    const customer = event.target.closest('[data-dc-customer]');
    if (customer) {
      document.querySelector('#dc-save [name=CustID]').value = customer.dataset.dcCustomer;
      document.getElementById('dc-customer-results').textContent = 'Đã chọn: ' + customer.dataset.name;
    }
    if (event.target.closest('[data-dc-add-line]')) {
      const total = document.getElementById('id_items-TOTAL_FORMS');
      const n = Number(total.value);
      if (n >= 50) { toast('Mỗi phiếu tối đa 50 món.', 'warning'); return; }
      const template = document.getElementById('dc-empty-line');
      document.getElementById('dc-lines').insertAdjacentHTML('beforeend', template.innerHTML.replaceAll('__prefix__', String(n)));
      total.value = n + 1;
      document.querySelector('#dc-lines .dc-line:last-child input').focus();
    }
  });
  document.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-dc-tab]');
    if (tab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[data-dc-tab]')];
      const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs.find(t => t !== tab);
      next.focus(); next.click();
    }
  });
  document.body.addEventListener('depositSaved', event => {
    window.closeKhblModal();
    toast(event.detail.message);
    htmx.trigger(document.body, 'depositRefresh');
  });
  document.body.addEventListener('htmx:afterSwap', event => {
    if (event.detail.target.id === 'modal-root') {
      const error = document.querySelector('[data-dc-error]');
      if (error) toast(error.textContent, 'error');
    }
  });
  ['htmx:responseError', 'htmx:sendError'].forEach(name => document.body.addEventListener(name, () => {
    toast('Không hoàn tất yêu cầu. Kiểm tra kết nối / quyền truy cập rồi tải lại danh sách.', 'error');
  }));
})();
