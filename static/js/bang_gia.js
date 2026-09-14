(() => {
  const form = document.getElementById('gia-form');
  if (!form) return;
  document.querySelectorAll('[data-status-url]').forEach(syncStatus => {
    const updateSyncButton = state => {
      const button = document.getElementById(syncStatus.dataset.syncButton);
      if (!button) return;
      const source = syncStatus.dataset.syncSource || 'SYNC';
      const detail = syncStatus.firstElementChild;
      const resolvedState = state || detail?.dataset.syncState || 'error';
      const count = Number(detail?.dataset.syncCount || 0);
      button.classList.remove('is-checking', 'is-synced', 'is-mismatch', 'is-error');
      button.classList.add(`is-${resolvedState}`);
      const icon = button.querySelector('[data-sync-icon]');
      const label = button.querySelector('[data-sync-label]');
      if (resolvedState === 'synced') {
        icon.textContent = '✓'; label.textContent = `${source} ĐÃ KHỚP`;
        button.title = `${source} đã khớp. Bấm để xem trạng thái đồng bộ.`;
      } else if (resolvedState === 'mismatch') {
        icon.textContent = '×'; label.textContent = `${source} LỆCH${count ? ` ${count}` : ''}`;
        button.title = `${source} có giá chênh lệch. Bấm để xem và duyệt đồng bộ.`;
      } else if (resolvedState === 'checking') {
        icon.textContent = '…'; label.textContent = `KIỂM TRA ${source}`;
      } else {
        icon.textContent = '×'; label.textContent = `${source} LỖI`;
        button.title = `Không kiểm tra được ${source}. Bấm để thử lại.`;
      }
    };
    const checkSource = async () => {
      updateSyncButton('checking');
      try {
        const response = await fetch(syncStatus.dataset.statusUrl, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        if (!response.ok) throw new Error('Không nhận được trạng thái nguồn giá.');
        syncStatus.innerHTML = await response.text();
        updateSyncButton();
      } catch (_) {
        syncStatus.innerHTML = '<div class="pg-gia__kk-status pg-gia__kk-status--error" data-sync-state="error"><b>× Không kiểm tra được nguồn giá</b><span>Hệ thống sẽ tự kiểm tra lại sau 10 phút.</span></div>';
        updateSyncButton();
      }
    };
    checkSource();
    window.addEventListener('gia:saved', checkSource);
    window.setInterval(checkSource, 10 * 60 * 1000);
  });
  const rows = [...form.querySelectorAll('[data-price-row]')];
  const format = value => new Intl.NumberFormat('vi-VN').format(value);
  const amount = value => /^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(value.trim()) ? Number(value.replace(/\./g, '')) : NaN;
  const updateSpread = row => {
    const buy = amount(row.querySelector('[data-amount="buy"]').value);
    const sell = amount(row.querySelector('[data-amount="sell"]').value);
    row.querySelector('[data-spread]').textContent = Number.isFinite(buy) && Number.isFinite(sell) ? format(sell - buy) : '—';
  };
  const statusLine = document.getElementById('gia-edit-status');
  const rowValue = row => JSON.stringify([...row.querySelectorAll('input:not([type="hidden"]),select')].map(f => f.type === 'checkbox' ? f.checked : f.value));
  const baseline = new Map(rows.map(row => [row, rowValue(row)]));
  const refreshDirty = () => {
    let count = 0;
    rows.forEach(row => {
      const dirty = rowValue(row) !== baseline.get(row);
      row.classList.toggle('is-dirty', dirty);
      row.querySelector('.pg-gia__row-state').textContent = dirty ? 'Chưa lưu' : 'Đã lưu';
      if (dirty) count++;
    });
    statusLine.textContent = count ? `${count} loại có thay đổi chưa lưu` : 'Giá đã lưu được sử dụng cho các phép tính mới.';
    return count;
  };
  const toast = (text, error = false) => {
    const item = document.createElement('div');
    item.className = error ? 'error' : 'success'; item.setAttribute('role', error ? 'alert' : 'status');
    item.textContent = text;
    document.getElementById('toast-root')?.append(item);
    item.addEventListener('click', () => item.remove());
    setTimeout(() => item.remove(), error ? 15000 : 5000);
  };
  rows.forEach(updateSpread);
  form.addEventListener('input', event => {
    refreshDirty();
    const row = event.target.closest('[data-price-row]');
    if (row) updateSpread(row);
  });
  form.querySelectorAll('[data-amount]').forEach(field => field.addEventListener('blur', () => {
    const value = amount(field.value);
    if (Number.isFinite(value)) field.value = format(value);
    refreshDirty();
  }));
  let saving = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving) return;
    const selected = event.submitter?.closest('[data-price-row]');
    const targets = selected ? [selected] : rows;
    for (const row of targets) {
      for (const field of row.querySelectorAll('input,select')) if (!field.reportValidity()) return;
    }
    const data = new FormData(form);
    if (selected) data.set('save_row', selected.querySelector('[name="row_id"]').value);
    saving = true;
    const controls = [...form.querySelectorAll('input,select,button')];
    const wasDisabled = controls.map(f => f.disabled);
    controls.forEach(f => f.disabled = true);
    statusLine.textContent = 'Đang lưu giá…';
    targets.forEach(row => row.classList.add('is-saving'));
    try {
      const response = await fetch(form.action, {method: 'POST', body: data, headers: {'Accept': 'application/json'}});
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'Chưa lưu được giá.');
      form.querySelector('[name="edit_token"]').value = result.edit_token;
      result.rows.forEach(saved => {
        const row = rows.find(r => r.dataset.goldType === saved.gold_type);
        if (!row || !targets.includes(row)) return;
        const oldId = row.querySelector('[name="row_id"]').value;
        row.querySelector('[name="row_id"]').value = saved.id;
        row.querySelector('[name="save_row"]').value = saved.id;
        for (const key of ['buy', 'sell', 'position', 'pinned', 'unit']) {
          const field = row.querySelector(`[name="${key}_${oldId}"]`);
          if (!field) continue;
          field.name = `${key}_${saved.id}`;
          if (key === 'pinned') field.checked = saved.pinned;
          else field.value = ['buy', 'sell'].includes(key) ? format(Number(saved[key])) : saved[key];
        }
        const date = new Date(saved.effective_at);
        const updated = row.querySelector('.pg-gia__updated');
        updated.textContent = date.toLocaleTimeString('vi-VN');
        const day = document.createElement('small'); day.textContent = date.toLocaleDateString('vi-VN'); updated.append(day);
        baseline.set(row, rowValue(row)); updateSpread(row);
        row.classList.add('is-saved'); setTimeout(() => row.classList.remove('is-saved'), 1800);
      });
      refreshDirty(); toast(result.message);
      window.dispatchEvent(new Event('gia:saved'));
      // Chỉ tải lại lịch sử; giữ nguyên các ô khác đang soạn.
      fetch(location.href).then(r => r.text()).then(html => {
        const history = new DOMParser().parseFromString(html, 'text/html').querySelector('.pg-gia__history');
        const current = document.querySelector('.pg-gia__history');
        if (history && current) { history.open = current.open; current.replaceWith(history); }
      }).catch(() => {});
    } catch (error) {
      statusLine.textContent = 'Chưa xác nhận lưu. Dữ liệu đang nhập vẫn được giữ lại.';
      toast(error.message || 'Không nhận được kết quả lưu. Hãy kiểm tra kết nối.', true);
    } finally {
      controls.forEach((f, i) => f.disabled = wasDisabled[i]);
      targets.forEach(row => row.classList.remove('is-saving'));
      saving = false;
    }
  });
  window.addEventListener('beforeunload', event => {
    if (saving || rows.some(row => rowValue(row) !== baseline.get(row))) {event.preventDefault(); event.returnValue = '';}
  });

  const open = document.getElementById('gia-view');
  const dialog = document.getElementById('gia-preview');
  const frame = document.getElementById('gia-preview-frame');
  const stage = dialog.querySelector('.pg-gia-preview__stage');
  const viewport = dialog.querySelector('.pg-gia-preview__viewport');
  const status = document.getElementById('gia-preview-status');
  const save = document.getElementById('gia-save-png');
  const download = document.getElementById('gia-png-download');
  let boardSize = {width:1160,height:520};
  const fit = () => {
    const scale = Math.min(1, viewport.clientWidth / boardSize.width);
    frame.style.transform = `scale(${scale})`;
    stage.style.width = `${boardSize.width * scale}px`;
    stage.style.height = `${boardSize.height * scale}px`;
  };
  new ResizeObserver(fit).observe(viewport);
  open.addEventListener('click', () => {
    save.disabled = true; status.textContent = 'Đang tải bảng giá đã lưu…';
    download.hidden = true;
    dialog.showModal();
    frame.src = open.dataset.boardUrl + '?preview=' + Date.now();
    fit();
  });
  frame.addEventListener('load', async () => {
    try {
      if (!frame.contentWindow.goldBoard) throw new Error('Không tải được bảng giá. Kiểm tra đăng nhập rồi mở lại.');
      boardSize = await frame.contentWindow.goldBoard.ready;
      frame.height = Math.ceil(boardSize.height);
      fit(); save.disabled = !boardSize.rows;
      status.textContent = boardSize.rows ? 'PNG chỉ chứa bảng giá, không gồm khung popup.' : 'Chưa ghim loại vàng nào. Chọn Ghim bảng và CẬP NHẬT trước.';
    } catch (error) {status.textContent = error.message; save.disabled = true;}
  });
  document.getElementById('gia-preview-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => open.focus());
  save.addEventListener('click', async () => {
    save.disabled = true; save.textContent = 'Đang tạo PNG…';
    try {
      const blob = await frame.contentWindow.goldBoard.exportPNG();
      const response = await fetch(dialog.dataset.saveUrl, {method:'POST', body:blob,
        headers:{'Content-Type':'image/png','X-CSRFToken':form.querySelector('[name="csrfmiddlewaretoken"]').value}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Không lưu được ảnh trên máy chủ.');
      download.href = result.url; download.download = result.filename; download.hidden = false;
      download.click();
      status.textContent = 'Đã lưu ảnh ' + result.filename;
    } catch (error) {status.textContent = 'Không lưu được PNG: ' + error.message;}
    finally {save.disabled = !boardSize.rows; save.textContent = 'LƯU PNG';}
  });
})();
