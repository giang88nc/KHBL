(() => {
  let busy = false, stopped = false, revision = '', delay = 15000;
  const root = () => document.getElementById('thau2-list');
  const message = text => { const el = document.getElementById('th2-scan-message'); if (el) el.textContent = text; };
  const refresh = () => {
    const form = root()?.querySelector('.th2-filter');
    if (form && !document.querySelector('#modal-root .th2-detail')) {
      return htmx.ajax('GET', form.getAttribute('hx-get') + '?' + new URLSearchParams(new FormData(form)), {target:'#thau2-list', swap:'outerHTML'});
    }
  };
  async function post(url, data) {
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value || JSON.parse(document.body.getAttribute('hx-headers') || '{}')['X-CSRFToken'];
    const response = await fetch(url, {method:'POST', body:data, credentials:'same-origin', headers:{'X-CSRFToken':csrf || ''}, signal:AbortSignal.timeout(25000)});
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || 'Đối soát không thành công.');
    return result;
  }
  async function scan(automatic) {
    const el = root(), form = el?.querySelector('.th2-filter');
    if (busy || !form || (!automatic && el.dataset.canManage !== '1') || (automatic && el.dataset.autoScan !== '1')) return;
    if (automatic && (document.hidden || form.elements.d2.value !== el.dataset.today || document.querySelector('#modal-root .th2-detail') || form.contains(document.activeElement))) return;
    const data = new FormData();
    data.set('d1', form.elements.d1.value); data.set('d2', form.elements.d2.value);
    data.set('action','scan'); data.set('automatic',automatic ? '1' : '0');
    busy = true;
    try {
      const result = await post(el.dataset.paymentUrl, data);
      if (!automatic || result.changed || (result.revision && result.revision !== revision)) await refresh();
      if (result.revision) revision = result.revision;
      delay = 15000;     // 22/09/2026: tự đối soát 15 giây/lần khi đến ngày = hôm nay
      message(result.message || 'Đã kiểm tra CK.');
    }
    catch (error) { delay = Math.min(delay * 2, 60000); message(error.message); }
    finally { busy = false; }
  }
  document.addEventListener('click', event => { if (event.target.closest('[data-payment-scan]')) scan(false); });
  document.addEventListener('submit', async event => {
    const form = event.target.closest('.th2-payment-form');
    if (!form) return;
    event.preventDefault();
    if (busy) return;
    const section = form.closest('.th2-payment'), output = section.querySelector('.th2-pay-message');
    const button = form.querySelector('button');
    busy = true; button.disabled = true; output.textContent = 'Đang kiểm tra…';
    try {
      const result = await post(form.action, new FormData(form));
      output.textContent = result.message;
      await htmx.ajax('GET', section.dataset.detailUrl, {target:'#modal-root', swap:'innerHTML'});
    } catch (error) { output.textContent = error.message; }
    finally { busy = false; button.disabled = false; }
  });
  async function tick() { if (stopped) return; await scan(true); if (!stopped) setTimeout(tick, delay); }
  window.addEventListener('pagehide', () => { stopped = true; });
  tick();
})();
