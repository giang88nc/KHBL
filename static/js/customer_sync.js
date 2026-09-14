(() => {
  'use strict';
  const dialog = document.getElementById('customer-sync');
  if (!dialog) return;
  const find = s => dialog.querySelector(s);
  const message = find('[data-sync-message]');
  const tabs = [...dialog.querySelectorAll('[data-sync-tab]')];
  let rows = [], target = '', canEdit = false, tab = 'ready', page = 1;
  let busy = false, loading = false, stop = false, changed = false;
  const size = 50;
  const normalized = text => String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/gi, 'd').toLowerCase();
  function tell(text, error = false) { message.textContent = text; message.classList.toggle('is-error', error); }
  function draw() {
    const counts = {ready: 0, existing: 0, attention: 0};
    rows.forEach(row => counts[row.status]++);
    tabs.forEach(button => {
      const selected = button.dataset.syncTab === tab;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      button.querySelector('b').textContent = counts[button.dataset.syncTab].toLocaleString('vi-VN');
    });
    find('#sync-panel').setAttribute('aria-labelledby', 'sync-tab-' + tab);
    const query = normalized(find('[data-sync-search]').value.trim());
    const filtered = rows.filter(row => row.status === tab && (!query || normalized([row.phone, row.name, row.address, row.id, row.cust_id, row.message].join(' ')).includes(query)));
    const pages = Math.max(1, Math.ceil(filtered.length / size)); page = Math.min(page, pages);
    const body = find('[data-sync-rows]'); body.replaceChildren();
    filtered.slice((page - 1) * size, page * size).forEach(row => {
      const tr = document.createElement('tr');
      [row.id, row.phone, row.name, row.address, [row.message, row.cust_id, row.pmv_name].filter(Boolean).join(' · ')].forEach(value => {
        const td = document.createElement('td'); td.textContent = value; tr.append(td);
      });
      const td = document.createElement('td');
      if (row.status === 'ready' || row.retry) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'khbl-btn khbl-btn--outline';
        button.textContent = row.retry ? 'Hoàn tất / kiểm tra lại' : '+ PMV'; button.disabled = busy || loading || !canEdit;
        button.addEventListener('click', () => run([row])); td.append(button);
      }
      tr.append(td); body.append(tr);
    });
    if (!filtered.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 6; td.textContent = loading ? 'Đang đối chiếu…' : 'Không có khách trong nhóm hoặc điều kiện tìm này.'; tr.append(td); body.append(tr); }
    find('[data-sync-page]').textContent = `Trang ${page}/${pages} · ${filtered.length.toLocaleString('vi-VN')} khách`;
    find('[data-sync-prev]').disabled = page <= 1;
    find('[data-sync-next]').disabled = page >= pages;
    find('[data-sync-all]').hidden = tab !== 'ready';
    find('[data-sync-all]').disabled = busy || loading || !canEdit || !counts.ready;
    find('[data-sync-all]').textContent = `THÊM TẤT CẢ (${counts.ready.toLocaleString('vi-VN')})`;
    find('[data-sync-reload]').disabled = busy || loading;
    find('[data-sync-stop]').hidden = !busy;
    find('[data-sync-stop]').disabled = stop;
  }
  async function jsonRequest(url, options) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Phiên đăng nhập hết hạn hoặc máy chủ chưa trả kết quả. Đăng nhập lại rồi tải đối chiếu.');
    const data = await response.json();
    if (!response.ok || data.error) {
      const error = new Error(data.error || `Máy chủ trả lỗi ${response.status}`); error.fatal = response.status === 503 || response.status === 403;
      throw error;
    }
    return data;
  }
  async function load() {
    if (busy || loading) return;
    loading = true; canEdit = false; draw(); tell('Đang đối chiếu PHONE với PMV…');
    try {
      const data = await jsonRequest(dialog.dataset.listUrl);
      rows = data.rows; target = data.target; canEdit = data.can_edit; page = 1;
      find('[data-sync-target]').textContent = `Đích: ${target === 'kk' ? 'MÁY KK — DỮ LIỆU THẬT' : 'BẢN THỬ'} · ${rows.length.toLocaleString('vi-VN')} dòng nguồn`;
      tell(canEdit ? 'Đối chiếu xong. Chọn + PMV hoặc THÊM TẤT CẢ để bắt đầu.' : 'Bạn có quyền xem; cần quyền Tạo / sửa KHÁCH HÀNG để sync.');
    } catch (error) { tell(error.message, true); }
    finally { loading = false; draw(); }
  }
  async function run(queue) {
    if (busy || loading || !canEdit || !queue.length) return;
    busy = true; stop = false; let done = 0, created = 0, existed = 0, failed = 0;
    const progress = find('[data-sync-progress]'); progress.hidden = false; progress.max = queue.length; progress.value = 0; draw();
    for (const row of queue) {
      if (stop) break;
      tell(`Đang xử lý ${done + 1}/${queue.length} · ${row.name} · ${row.phone}`);
      try {
        const result = await jsonRequest(dialog.dataset.insertUrl, {method: 'POST', headers: {
          'Content-Type': 'application/json', 'X-CSRFToken': find('[name=csrfmiddlewaretoken]').value
        }, body: JSON.stringify({id: row.id, fingerprint: row.fingerprint, target})});
        Object.assign(row, result, {retry: false});
        if (result.created) { created++; changed = true; } else existed++;
      } catch (error) {
        failed++; row.status = 'attention'; row.message = error.message; row.retry = true;
        // Mất kết nối/khóa ghi: giữ hàng đợi còn lại, không phát tiếp hàng nghìn yêu cầu lỗi.
        if (error.fatal || error instanceof TypeError || /đích PMV|đăng nhập/i.test(error.message)) stop = true;
      }
      done++; progress.value = done; draw();
    }
    busy = false;
    const summary = `${stop ? 'Đã dừng' : 'Hoàn tất lượt'}: ${created} thêm thành công · ${existed} đã có · ${failed} cần kiểm tra · ${queue.length - done} chưa chạy.`;
    tell(summary, failed > 0);
    document.dispatchEvent(new CustomEvent('customerSyncDone', {detail: {message: summary, failed}}));
    if (changed) refreshCustomers(); draw();
  }
  function refreshCustomers() {
    const filter = document.querySelector('form.kh-loc');
    if (filter && window.htmx) window.htmx.trigger(filter, 'submit');
    changed = false;
  }
  function close() { stop = true; dialog.close(); if (changed) refreshCustomers(); }
  document.querySelector('[data-customer-sync-open]').addEventListener('click', () => { dialog.showModal(); if (!busy) load(); });
  dialog.querySelectorAll('[data-sync-close]').forEach(button => button.addEventListener('click', close));
  dialog.addEventListener('cancel', () => { stop = true; });
  tabs.forEach((button, index) => {
    button.addEventListener('click', () => { tab = button.dataset.syncTab; page = 1; draw(); });
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click(); tabs[next].focus();
    });
  });
  find('[data-sync-search]').addEventListener('input', () => { page = 1; draw(); });
  find('[data-sync-prev]').addEventListener('click', () => { page--; draw(); });
  find('[data-sync-next]').addEventListener('click', () => { page++; draw(); });
  find('[data-sync-reload]').addEventListener('click', load);
  find('[data-sync-all]').addEventListener('click', () => run(rows.filter(row => row.status === 'ready')));
  find('[data-sync-stop]').addEventListener('click', () => { stop = true; draw(); });
  window.addEventListener('beforeunload', event => { if (busy) { event.preventDefault(); event.returnValue = ''; } });
})();
