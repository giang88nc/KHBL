(() => {
  const form = document.getElementById('gia-form');
  if (!form) return;
  const rows = [...form.querySelectorAll('[data-price-row]')];
  const format = value => new Intl.NumberFormat('vi-VN').format(value);
  const amount = value => /^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(value.trim()) ? Number(value.replace(/\./g, '')) : NaN;
  const updateSpread = row => {
    const buy = amount(row.querySelector('[data-amount="buy"]').value);
    const sell = amount(row.querySelector('[data-amount="sell"]').value);
    row.querySelector('[data-spread]').textContent = Number.isFinite(buy) && Number.isFinite(sell) ? format(sell - buy) : '—';
  };
  rows.forEach(updateSpread);
  form.addEventListener('input', event => {
    document.getElementById('gia-edit-status').textContent = 'Có thay đổi chưa lưu · Bấm CẬP NHẬT để lưu toàn bộ.';
    const row = event.target.closest('[data-price-row]');
    if (row) updateSpread(row);
  });
  form.querySelectorAll('[data-amount]').forEach(field => field.addEventListener('blur', () => {
    const value = amount(field.value);
    if (Number.isFinite(value)) field.value = format(value);
  }));
  form.addEventListener('submit', () => {
    const button = document.getElementById('gia-submit');
    button.disabled = true; button.textContent = 'Đang cập nhật…';
    document.getElementById('gia-edit-status').textContent = 'Đang lưu MySQL và đồng bộ MSSQL…';
  });
  window.addEventListener('pageshow', () => {
    const button = document.getElementById('gia-submit');
    if (button.textContent === 'Đang cập nhật…') {button.disabled = false; button.textContent = 'CẬP NHẬT';}
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
