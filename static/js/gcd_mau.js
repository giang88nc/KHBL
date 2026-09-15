/* ──────────────────────────────────────────────────────────────────────────────────────────────
   Trang cấu hình MẪU IN GIẤY CẦM ĐỒ — /he-thong/mau-in-gcd/ (15/09/2026).

   ⚠ buildCss() PHẢI sinh chuỗi Y HỆT apps/pos/gcd_layout.css() — sửa một nơi là phải sửa nơi kia,
     nếu không thì "cái đang nhìn" khác "cái server sẽ in". Sau mỗi lần LƯU, server trả luôn chuỗi
     CSS của chính nó và trang thay thẳng vào <style> → lệch là lộ ra ngay lần lưu đầu tiên.
   ⚠ so() thay cho "%g" của Python: mọi số đã được làm tròn 2 số lẻ trước khi vào chuỗi.
   ────────────────────────────────────────────────────────────────────────────────────────────── */
(function () {
  var cfg = document.getElementById('gcdm-cfg');
  if (!cfg) return;
  var J = function (id) { var e = document.getElementById(id); return e ? JSON.parse(e.textContent) : null; };
  var SEL = J('gcdm-sel'), KHO = J('gcdm-kho'), CACH = J('gcdm-cach'), CACH_THAT = J('gcdm-cach-that');
  var MAC_DINH = J('gcdm-defaults'), MI_MAC_DINH = J('gcdm-mayin-defaults');
  var KHO_TO = cfg.dataset.khoTo, DUOC_SUA = cfg.dataset.sua === '1';
  var state = J('gcdm-state'), mayin = J('gcdm-mayin'), sel = null;

  var paper = document.getElementById('gcd-to');
  var nenEl = document.getElementById('gcd-nen');
  var styleEl = document.getElementById('gcd-layout-css');
  var vp = document.getElementById('gcdm-vp'), scaleEl = document.getElementById('gcdm-scale');
  var status = document.getElementById('gcdm-status');

  var LIM = { left: [0, 100], top: [0, 100], w: [1, 100], h: [0.5, 100], fs: [3, 30] };
  var LIM_IN = { dx: [-80, 80], dy: [-80, 80], ty_le: [50, 150], kho_w: [80, 420], kho_h: [60, 420] };
  var LIM_NEN = { x: [-30, 30], y: [-30, 30], w: [50, 200], h: [50, 200] };

  function so(v) { return String(+v); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, Math.round(v * 100) / 100)); }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }

  /* ── sinh CSS: bản sao 1-1 của gcd_layout.css() ── */
  function cssIn(i) {
    var size = KHO[i.kho] || 'auto';
    if (size === KHO_TO) size = so(i.kho_w) + 'mm ' + so(i.kho_h) + 'mm';
    var giua = i.canh !== 'trai';
    var r = ['left:' + so(i.dx) + 'mm!important', 'top:' + so(i.dy) + 'mm!important',
      giua ? 'margin:0 auto!important' : 'margin:0!important',
      'background:none!important', 'box-shadow:none!important', 'overflow:hidden!important'];
    var ty = +i.ty_le;
    if (ty !== 100) r.push('transform:scale(' + so(Math.round(ty / 100 * 1e6) / 1e6) + ')!important;transform-origin:top ' + (giua ? 'center' : 'left') + '!important');
    return '@media print{@page{size:' + size + ';margin:0}.gcd-nen,.no-print{display:none!important}.gcd-a5{' + r.join(';') + '}}';
  }
  function buildCss(st) {
    var i = st._in, w = so(i.kho_w), h = so(i.kho_h);
    var out = ['.gcd-a5{position:relative!important;width:' + w + 'mm!important;max-width:100%;height:auto;aspect-ratio:' + w + '/' + h + ';overflow:hidden}'];
    Object.keys(SEL).forEach(function (k) {
      var v = st[k], s = SEL[k]; if (!v) return;
      var fs = so(v.fs);
      out.push(s + '{position:absolute!important;left:' + so(v.left) + '%!important;top:' + so(v.top) + '%!important;width:' + so(v.w) + '%!important;height:' + so(v.h) + '%!important;font-size:' + fs + 'pt!important;--gcd-fs:' + fs + 'pt}');
      out.push(s + '.gcd-co-2{font-size:calc(' + fs + 'pt*.88)!important}');
      out.push(s + '.gcd-co-3{font-size:calc(' + fs + 'pt*.76)!important}');
      if (v.an) {
        out.push('.gcd-a5:not(.gcd-sua) ' + s + '{display:none!important}');
        out.push('.gcd-a5.gcd-sua ' + s + '{opacity:.3!important;outline:1px dashed #B3402A!important}');
      }
    });
    out.push(cssIn(i));
    return out.join('\n');
  }

  /* ── vẽ lại ── */
  function veNen() {
    var n = state._nen || { x: 0, y: 0, w: 100, h: 100 };
    nenEl.style.backgroundImage = 'url("' + cfg.dataset.nen + '")';
    nenEl.style.backgroundPosition = n.x + '% ' + n.y + '%';
    nenEl.style.backgroundSize = n.w + '% ' + n.h + '%';
  }
  function doThuPhong() {
    var chon = document.getElementById('gcdm-k').value, k;
    if (chon === '0') k = Math.min(1, (vp.clientWidth || 1) / (paper.offsetWidth || 1));
    else k = +chon;
    scaleEl.style.setProperty('--gcd-k', k);
    vp.style.height = (paper.offsetHeight * k) + 'px';
  }
  function apply() { styleEl.textContent = buildCss(state); veNen(); doThuPhong(); }

  function sync() {
    document.querySelectorAll('.gcdm-row').forEach(function (row) {
      var k = row.dataset.key, v = state[k] || {};
      row.classList.toggle('is-sel', k === sel);
      row.classList.toggle('is-an', !!v.an);
      row.querySelectorAll('input').forEach(function (inp) {
        if (document.activeElement === inp) return;
        if (inp.dataset.p === 'an') inp.checked = !v.an;      /* cột "In" = NGƯỢC với an */
        else inp.value = v[inp.dataset.p];
      });
    });
    paper.querySelectorAll('[data-gcd]').forEach(function (el) {
      el.classList.toggle('is-sel', el.dataset.gcd === sel);
    });
    document.querySelectorAll('[data-in]').forEach(function (el) {
      if (document.activeElement !== el) el.value = state._in[el.dataset.in];
    });
    document.querySelectorAll('[data-ct]').forEach(function (el) {
      if (document.activeElement !== el) el.value = state._ct[el.dataset.ct];
    });
    document.querySelectorAll('[data-nen]').forEach(function (el) {
      if (document.activeElement !== el) el.value = state._nen[el.dataset.nen];
    });
  }
  function chon(k) {
    sel = k; sync();
    var row = document.querySelector('.gcdm-row[data-key="' + k + '"]');
    if (row) row.scrollIntoView({ block: 'nearest' });
  }
  function hopLe(k) {
    var v = state[k];
    v.w = clamp(v.w, 1, 100); v.h = clamp(v.h, 0.5, 100);
    v.left = clamp(v.left, 0, 100 - v.w); v.top = clamp(v.top, 0, 100 - v.h);
    v.fs = clamp(v.fs, 3, 30);
  }

  /* ── bảng số + cột In ── */
  document.querySelectorAll('.gcdm-row').forEach(function (row) {
    row.addEventListener('click', function (e) { if (e.target.tagName !== 'INPUT') chon(row.dataset.key); });
    row.querySelectorAll('input').forEach(function (inp) {
      inp.addEventListener('focus', function () { chon(row.dataset.key); });
      if (inp.dataset.p === 'an') {
        inp.addEventListener('change', function () {
          state[row.dataset.key].an = inp.checked ? 0 : 1; apply(); sync();
        });
        return;
      }
      inp.addEventListener('input', function () {
        var n = parseFloat(inp.value); if (isNaN(n) || inp.value === '') return;
        var p = inp.dataset.p;
        state[row.dataset.key][p] = clamp(n, LIM[p][0], LIM[p][1]);
        hopLe(row.dataset.key); apply(); sync();
      });
    });
  });

  /* ── máy in / nội dung / nền ── */
  document.querySelectorAll('[data-in]').forEach(function (el) {
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', function () {
      var p = el.dataset.in;
      if (LIM_IN[p]) { var n = parseFloat(el.value); if (isNaN(n)) return; state._in[p] = clamp(n, LIM_IN[p][0], LIM_IN[p][1]); }
      else state._in[p] = el.value;
      apply(); sync();
    });
  });
  document.querySelectorAll('[data-ct]').forEach(function (el) {
    el.addEventListener('change', function () { state._ct[el.dataset.ct] = el.value; });
  });
  document.querySelectorAll('[data-nen]').forEach(function (el) {
    el.addEventListener('input', function () {
      var p = el.dataset.nen, n = parseFloat(el.value); if (isNaN(n)) return;
      state._nen[p] = clamp(n, LIM_NEN[p][0], LIM_NEN[p][1]); veNen();
    });
  });
  document.getElementById('gcdm-k').addEventListener('change', doThuPhong);
  document.getElementById('gcdm-nen-hien').addEventListener('change', function () {
    nenEl.style.display = this.checked ? '' : 'none';
  });
  window.addEventListener('resize', doThuPhong);

  /* ── kéo-thả trên tờ giấy (mẫu số = getBoundingClientRect nên đúng cả khi đang thu phóng) ── */
  var drag = null;
  paper.addEventListener('mousedown', function (e) {
    var el = e.target.closest('[data-gcd]'); if (!el) return;
    e.preventDefault();
    var k = el.dataset.gcd; chon(k);
    if (!DUOC_SUA) return;
    var r = paper.getBoundingClientRect();
    drag = { k: k, x0: e.clientX, y0: e.clientY, left: state[k].left, top: state[k].top, w: r.width, h: r.height };
  });
  window.addEventListener('mousemove', function (e) {
    if (!drag) return;
    state[drag.k].left = clamp(drag.left + (e.clientX - drag.x0) / drag.w * 100, 0, 100);
    state[drag.k].top = clamp(drag.top + (e.clientY - drag.y0) / drag.h * 100, 0, 100);
    hopLe(drag.k); apply(); sync();
  });
  window.addEventListener('mouseup', function () { drag = null; });
  document.addEventListener('keydown', function (e) {
    if (!sel || !DUOC_SUA || e.target.matches('input,select,textarea')) return;
    var v = state[sel], step = e.shiftKey ? 1 : 0.2, di = true;
    if (e.key === 'ArrowLeft') v.left = clamp(v.left - step, 0, 100);
    else if (e.key === 'ArrowRight') v.left = clamp(v.left + step, 0, 100);
    else if (e.key === 'ArrowUp') v.top = clamp(v.top - step, 0, 100);
    else if (e.key === 'ArrowDown') v.top = clamp(v.top + step, 0, 100);
    else di = false;
    if (di) { e.preventDefault(); hopLe(sel); apply(); sync(); }
  });

  /* ── SỔ MÁY IN ── */
  var miWrap = document.getElementById('gcdm-mi'), miChon = document.getElementById('gcdm-mayin-chon');
  function veMayIn() {
    miWrap.innerHTML = '';
    mayin.ban.forEach(function (b, idx) {
      var row = document.createElement('div');
      row.className = 'gcdm-mi__row' + (b.bat ? '' : ' is-tat');
      var cachOpts = Object.keys(CACH).map(function (c) {
        return '<option value="' + c + '"' + (b.cach === c ? ' selected' : '') + '>' + CACH[c] + '</option>';
      }).join('');
      var badge = CACH_THAT.indexOf(b.cach) < 0 ? ' <span class="gcdm-badge">chưa nối</span>' : '';
      var mang = b.cach === 'trinh_duyet' ? '' :
        '<input data-mi="host" placeholder="Địa chỉ IP / tên máy" value="' + (b.host || '') + '">' +
        '<input data-mi="cong" type="number" min="0" max="65535" placeholder="Cổng" value="' + (b.cong || 0) + '">' +
        '<input data-mi="hang_doi" placeholder="Hàng đợi (queue)" value="' + (b.hang_doi || '') + '">' +
        '<input data-mi="ten_windows" placeholder="Tên máy in trong Windows" value="' + (b.ten_windows || '') + '">';
      row.innerHTML =
        '<input type="radio" name="mi-mac-dinh" data-mi-md="' + idx + '"' + (mayin.mac_dinh === b.id ? ' checked' : '') + ' title="Máy in mặc định">' +
        '<div><input data-mi="ten" placeholder="Tên máy in" value="' + (b.ten || '') + '">' +
        '<select data-mi="cach">' + cachOpts + '</select>' + mang +
        '<input data-mi="ghi_chu" placeholder="Ghi chú" value="' + (b.ghi_chu || '') + '">' +
        '<label style="font-size:11px"><input type="checkbox" data-mi="bat"' + (b.bat ? ' checked' : '') + '> Đang dùng</label>' +
        '<div style="font-size:10px;color:#7a6a58">mã: ' + b.id + badge + '</div></div>' +
        (DUOC_SUA ? '<button type="button" class="gcdm-mi__x" data-mi-xoa="' + idx + '" title="Xoá">✕</button>' : '<span></span>');
      row.querySelectorAll('[data-mi]').forEach(function (el) {
        el.disabled = !DUOC_SUA;
        el.addEventListener('change', function () {
          var p = el.dataset.mi;
          b[p] = el.type === 'checkbox' ? el.checked : (p === 'cong' ? (parseInt(el.value, 10) || 0) : el.value);
          veMayIn();
        });
      });
      var rd = row.querySelector('[data-mi-md]');
      rd.disabled = !DUOC_SUA;
      rd.addEventListener('change', function () { mayin.mac_dinh = b.id; veMayIn(); });
      var x = row.querySelector('[data-mi-xoa]');
      if (x) x.addEventListener('click', function () {
        mayin.ban.splice(idx, 1);
        if (mayin.mac_dinh === b.id) mayin.mac_dinh = mayin.ban.length ? mayin.ban[0].id : '';
        veMayIn();
      });
      miWrap.appendChild(row);
    });
    miChon.innerHTML = '<option value="">— chưa chọn —</option>' + mayin.ban.map(function (b) {
      return '<option value="' + b.id + '"' + (state._in.may_in === b.id ? ' selected' : '') + '>' + b.ten + '</option>';
    }).join('');
    miChon.disabled = !DUOC_SUA;
  }
  var themBtn = document.getElementById('gcdm-mi-them');
  if (themBtn) themBtn.addEventListener('click', function () {
    var n = 1, ma;
    do { ma = 'may_' + n++; } while (mayin.ban.some(function (b) { return b.id === ma; }));
    mayin.ban.push({ id: ma, ten: 'Máy in ' + (mayin.ban.length + 1), cach: 'trinh_duyet',
      host: '', cong: 0, hang_doi: '', ten_windows: '', bat: true, ghi_chu: '' });
    if (!mayin.mac_dinh) mayin.mac_dinh = ma;
    veMayIn();
  });

  /* ── MÁY IN THẬT: TÌM → CHỌN → LƯU (khoá riêng gcd_may_in) ────────────────────────────────
     Cố ý KHÔNG đi chung nút LƯU MẪU: chọn máy in gửi sang 2 endpoint riêng nên thao tác này không
     bao giờ có cơ hội ghi đè bố cục đã căn từng milimet. Máy chủ chưa cắm máy in thật thì danh sách
     toàn MÁY IN ẢO — vẫn cho chọn nhưng gắn nhãn và nói thẳng là in ra tệp, không ra giấy. */
  var mi2 = J('gcdm-mi2-state') || { ten: '', cong: '', ao: false, luc: '', boi: '' };
  var mi2Ten = document.getElementById('gcdm-mi2-ten');
  var mi2Phu = document.getElementById('gcdm-mi2-phu');
  var mi2Ds = document.getElementById('gcdm-mi2-ds');
  var mi2BaoEl = document.getElementById('gcdm-mi2-bao');
  var mi2Danh = null;          /* null = chưa bấm Tìm lần nào */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function mi2Bao(t, loi) {
    if (!mi2BaoEl) return;
    mi2BaoEl.textContent = t || '';
    mi2BaoEl.className = loi ? 'is-loi' : '';
  }
  function guiJSON(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.querySelector('[name=csrfmiddlewaretoken]').value },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () {
        return { ok: false, loi: 'Máy chủ trả về dữ liệu không đọc được (mã ' + r.status + ').' };
      });
    });
  }

  function mi2VeDangChon() {
    if (!mi2Ten) return;
    mi2Ten.textContent = mi2.ten || '— chưa chọn máy in —';
    if (mi2Phu) {
      mi2Phu.textContent = mi2.ten
        ? ((mi2.cong ? 'cổng ' + mi2.cong + ' · ' : '') + 'lưu lúc ' + (mi2.luc || '?')
           + (mi2.boi ? ' bởi ' + mi2.boi : '')
           + (mi2.ao ? ' · ⚠ đây là MÁY IN ẢO, in ra tệp chứ không ra giấy' : ''))
        : 'Để trống = mỗi lần in đều mở hộp thoại in của trình duyệt để tự chọn máy in.';
    }
  }
  function mi2VeDanhSach(giai_thich, loi) {
    if (!mi2Ds) return;
    if (mi2Danh === null) { mi2Ds.innerHTML = ''; return; }
    var h = '';
    if (loi) h += '<div class="gcdm-mi2__loi">' + esc(loi) + '</div>';
    if (giai_thich) h += '<div class="gcdm-mi2__trong">' + esc(giai_thich) + '</div>';
    h += mi2Danh.map(function (p, i) {
      var dang = mi2.ten && p.ten === mi2.ten;
      var nhan = (p.mac_dinh ? '<span class="gcdm-badge">mặc định của Windows</span>' : '')
        + (p.ao ? '<span class="gcdm-badge is-ao">ẢO</span>' : '');
      return '<div class="gcdm-mi2__row' + (p.ao ? ' is-ao' : '') + (dang ? ' is-chon' : '') + '">'
        + (DUOC_SUA ? '<button type="button" class="khbl-btn khbl-btn--outline" data-mi2="' + i + '">'
            + (dang ? '✔ đang dùng' : 'Dùng máy này') + '</button>' : '<span></span>')
        + '<div><b>' + esc(p.ten) + '</b> ' + nhan
        + '<div class="gcdm-mi2__phu">' + esc(p.trang_thai || '—') + ' · cổng ' + esc(p.cong || '—')
        + (p.ly_do_ao ? ' · ' + esc(p.ly_do_ao) : '') + '</div></div></div>';
    }).join('');
    mi2Ds.innerHTML = h;
    mi2Ds.querySelectorAll('[data-mi2]').forEach(function (b) {
      b.addEventListener('click', function () { mi2Chon(mi2Danh[+b.dataset.mi2]); });
    });
  }
  function mi2Chon(p) {
    if (!p) return;
    mi2Bao('Đang lưu…');
    guiJSON(cfg.dataset.urlChon, { may_in: { ten: p.ten, cong: p.cong, ao: !!p.ao } })
      .then(function (j) {
        if (!j.ok) { mi2Bao(j.loi || 'Lưu không được.', true); return; }
        mi2 = j.may_in;
        mi2VeDangChon(); mi2VeDanhSach();
        mi2Bao('Đã lưu máy in vào cấu hình.');
      }).catch(function (e) { mi2Bao('Lưu không được: ' + e.message, true); });
  }

  var btnTim = document.getElementById('gcdm-mi2-tim');
  if (btnTim) btnTim.addEventListener('click', function () {
    btnTim.disabled = true;
    mi2Bao('Đang hỏi Windows…');
    guiJSON(cfg.dataset.urlTim, {}).then(function (j) {
      mi2Danh = j.ds || [];
      if (j.dang_chon) { mi2 = j.dang_chon; mi2VeDangChon(); }
      mi2VeDanhSach(j.giai_thich, j.loi);
      mi2Bao(j.ds && j.ds.length ? 'Tìm được ' + j.ds.length + ' máy in.'
        : (j.loi ? '' : 'Máy chủ chưa có máy in nào.'), !!j.loi);
    }).catch(function (e) {
      mi2Danh = [];
      mi2VeDanhSach('', 'Không hỏi được máy chủ: ' + e.message);
      mi2Bao('Tìm không được.', true);
    }).then(function () { btnTim.disabled = false; });
  });
  var btnBo = document.getElementById('gcdm-mi2-bo');
  if (btnBo) btnBo.addEventListener('click', function () {
    mi2Bao('Đang bỏ chọn…');
    guiJSON(cfg.dataset.urlChon, { bo_chon: true }).then(function (j) {
      if (!j.ok) { mi2Bao(j.loi || 'Bỏ chọn không được.', true); return; }
      mi2 = j.may_in;
      mi2VeDangChon(); mi2VeDanhSach();
      mi2Bao('Đã bỏ chọn — mỗi lần in sẽ mở hộp thoại in của trình duyệt.');
    }).catch(function (e) { mi2Bao('Bỏ chọn không được: ' + e.message, true); });
  });
  mi2VeDangChon();

  /* ── lưu / đặt lại / in thử ── */
  function bao(t, loi) { status.textContent = t; status.classList.toggle('error', !!loi); }
  function post(payload) {
    return fetch(cfg.dataset.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.querySelector('[name=csrfmiddlewaretoken]').value },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); });
  }
  function luu() {
    return post({ layout: state, may_in: mayin }).then(function (j) {
      if (!j.ok) { bao('Lưu lỗi: ' + (j.loi || '?'), true); return false; }
      state = j.layout; mayin = j.may_in;
      styleEl.textContent = j.css;            /* dùng CSS CỦA SERVER — đang nhìn = sắp in */
      veNen(); doThuPhong(); sync(); veMayIn();
      bao('Đã lưu mẫu in GIẤY CẦM ĐỒ. Trang lập phiếu bên KHCD in theo bố cục này ngay.');
      return true;
    }).catch(function (e) { bao('Lưu lỗi: ' + e.message, true); return false; });
  }
  var btnLuu = document.getElementById('gcdm-save');
  if (btnLuu) btnLuu.addEventListener('click', luu);
  var btnReset = document.getElementById('gcdm-reset');
  if (btnReset) btnReset.addEventListener('click', function () {
    state = clone(MAC_DINH); mayin = clone(MI_MAC_DINH);
    apply(); sync(); veMayIn();
    bao('Đang XEM TRƯỚC bản mặc định — bấm LƯU MẪU mới ghi vào hệ thống.');
  });
  /* IN THỬ: bỏ class gcd-sua để khối TẮT không in ra (giống lúc in thật bên KHCD), in xong trả lại. */
  window.addEventListener('beforeprint', function () { paper.classList.remove('gcd-sua'); });
  window.addEventListener('afterprint', function () { paper.classList.add('gcd-sua'); });
  var btnIn = document.getElementById('gcdm-print');
  if (btnIn) btnIn.addEventListener('click', function () {
    luu().then(function (ok) { if (ok) window.print(); });
  });

  apply(); sync(); veMayIn(); chon('khach_ten');
})();
