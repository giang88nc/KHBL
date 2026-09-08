(() => {
  const form = document.getElementById('ck-filter');
  if (!form) return;
  let busy = false;
  let applied = new URLSearchParams(new FormData(form));
  const status = document.getElementById('ck-status');
  const matchStatus = document.getElementById('ck-reconcile-status');
  let matchBusy = false, matchTimer = null, matchController = null, leaving = false;
  function isToday() {
    const parts = new Intl.DateTimeFormat('en-US', {timeZone:'Asia/Ho_Chi_Minh', year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date());
    const p = Object.fromEntries(parts.map(x => [x.type, x.value]));
    return applied.get('d2') === `${p.year}-${p.month}-${p.day}`;
  }
  async function match() {
    clearTimeout(matchTimer);
    if (!matchStatus || matchBusy || leaving || document.hidden) return;
    if (!isToday()) { matchStatus.textContent = ''; return; }
    matchBusy = true;
    matchController = new AbortController();
    const abortTimer = setTimeout(() => matchController?.abort(), 10000);
    try {
      const response = await fetch(matchStatus.dataset.url, {
        method:'POST', credentials:'same-origin', cache:'no-store', signal:matchController.signal,
        headers:{'X-CSRFToken':matchStatus.dataset.csrf, 'Content-Type':'application/x-www-form-urlencoded'},
        body:new URLSearchParams({d2:applied.get('d2')})
      });
      const result = await response.json();
      if (document.hidden || leaving || !isToday()) return;
      if (result.status === 'busy') return;
      if (result.status === 'skipped') { matchStatus.textContent = ''; return; }
      matchStatus.textContent = result.status === 'error' ? result.message :
        `Đối soát: ${result.matched || 0} khớp mới · ${result.pending || 0} khoản RA chưa đối soát` +
        (result.needs_review ? ` · ${result.needs_review} cần kiểm tra. ` + (result.issues || []).map(x => `#${x.id}: ${x.message}`).join(' · ') : '');
      if (result.matched) await refresh(null, false);
    } catch (_) {
      if (!document.hidden && !leaving && isToday()) matchStatus.textContent = 'Đối soát tạm gián đoạn. Sẽ thử lại; bảng ngân hàng vẫn xem được.';
    } finally {
      clearTimeout(abortTimer);
      matchController = null; matchBusy = false;
      if (!document.hidden && !leaving && isToday()) matchTimer = setTimeout(match, 5000);
    }
  }
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
    if (e.detail.elt === form && e.detail.successful) match();
  });
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-ck-page]');
    if (button) refresh(button.dataset.ckPage, false);
  });
  const timer = setInterval(() => refresh(null, true), 3000);
  document.addEventListener('visibilitychange', () => {
    clearTimeout(matchTimer);
    if (!document.hidden) { refresh(null, true); match(); }
  });
  window.addEventListener('pagehide', () => { leaving = true; clearTimeout(matchTimer); matchController?.abort(); });
  window.addEventListener('pageshow', () => { leaving = false; match(); });
  match();
})();
