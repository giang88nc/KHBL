(() => {
  const form = document.getElementById('ck-filter');
  if (!form) return;
  let busy = false;
  let applied = new URLSearchParams(new FormData(form));
  const status = document.getElementById('ck-status');
  async function refresh(page, polling) {
    if (busy || document.hidden || !document.getElementById('ck-results')) return;
    const result = document.getElementById('ck-results');
    busy = true;
    const params = new URLSearchParams(applied);
    params.set('page', page || result.dataset.page || '1');
    if (polling && result.dataset.signature) params.set('signature', result.dataset.signature);
    try {
      await htmx.ajax('GET', location.pathname, {source:result, target:'#ck-results', swap:'outerHTML', values:Object.fromEntries(params)});
    } catch (_) { status.textContent = 'Mất kết nối. Đang chờ thử lại…'; }
    finally { busy = false; }
  }
  document.body.addEventListener('htmx:beforeRequest', e => {
    if (e.detail.elt === form) {
      htmx.trigger(document.getElementById('ck-results'), 'htmx:abort');
      applied = new URLSearchParams(new FormData(form)); busy = true;
    }
  });
  document.body.addEventListener('htmx:afterRequest', e => {
    if (e.detail.target?.id !== 'ck-results') return;
    busy = false;
    status.textContent = e.detail.successful ? '' : 'Không tải được dữ liệu. Đang chờ thử lại…';
  });
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-ck-page]');
    if (button) refresh(button.dataset.ckPage, false);
  });
  const timer = setInterval(() => refresh(null, true), 3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(null, true); });
  window.addEventListener('pagehide', () => clearInterval(timer), {once:true});
})();
