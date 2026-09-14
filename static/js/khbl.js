/* KHBL — JS chung. MỘT hàm khblBind(root) duy nhất, gọi lại sau mỗi lần HTMX thay DOM. */
(function () {
  "use strict";

  /* ---------- tiền: chấm nghìn khi gõ ---------- */
  function bindMoney(root) {
    root.querySelectorAll("input.khbl-money:not([data-money])").forEach(function (el) {
      el.dataset.money = "1";
      el.addEventListener("input", function () {
        var d = el.value.replace(/[^\d]/g, "");
        el.value = d ? d.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
      });
    });
  }

  /* ---------- ô quét: đếm ký tự + nhận diện đầu đọc ---------- */
  function bindScan(root) {
    root.querySelectorAll(".khbl-scan__in:not([data-scan])").forEach(function (el) {
      el.dataset.scan = "1";
      var dots = el.closest(".khbl-scan") ? el.closest(".khbl-scan").querySelector(".khbl-scan__dots") : null;
      var last = 0, fast = 0, timer = null;

      function paint() {
        if (!dots) return;
        var n = el.value.trim().length;
        Array.prototype.forEach.call(dots.children, function (d, i) {
          d.className = i < n ? (n === 9 && i === 8 ? "warn" : "on") : "";
        });
      }
      el.addEventListener("input", function (e) {
        var now = e.timeStamp || 0;
        if (now - last < 50) { fast++; } else if (last) { fast = 0; }
        last = now;
        paint();
        // đầu đọc mã vạch gõ rất nhanh; nếu nó không tự gửi Enter thì tự nộp sau 120ms im lặng
        if (timer) clearTimeout(timer);
        if (fast >= 3 && el.value.trim().length >= 6) {
          timer = setTimeout(function () { submitScan(el); }, 120);
        }
      });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); if (timer) clearTimeout(timer); submitScan(el); }
      });
      paint();
    });
  }
  function submitScan(el) {
    if (!el.value.trim()) return;
    var f = el.closest("form");
    if (f && window.htmx) window.htmx.trigger(f, "khbl:scan");
    else if (f) f.requestSubmit();
  }

  /* ---------- popup ---------- */
  window.closeKhblModal = function () {
    if (window.__khblStopCamera) window.__khblStopCamera();
    var r = document.getElementById("modal-root");
    if (r) r.innerHTML = "";
  };
  /* popup ✂ LỒNG trong popup khách (#kh-cat-root, 10/09/2026): đóng riêng, popup khách phía dưới còn nguyên */
  window.khblKhCatDong = function () {
    var r = document.getElementById("kh-cat-root");
    if (r) r.innerHTML = "";
  };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      // Lớp CON đang mở bên trong popup (xem ảnh, khung phụ…) tự lo phím Esc của nó — đừng đóng luôn popup cha.
      // Lớp con chỉ cần gắn thuộc tính data-khbl-lop-con và tự ẩn mình khi nhận Esc.
      if (document.querySelector("[data-khbl-lop-con]:not([hidden])")) return;
      var k = document.getElementById("kh-cat-root");
      if (k && k.innerHTML.trim()) { e.stopPropagation(); window.khblKhCatDong(); return; }
      var r = document.getElementById("modal-root");
      if (r && r.innerHTML.trim()) { e.stopPropagation(); window.closeKhblModal(); }
    }
  }, true);
  function bindModal(root) {
    root.querySelectorAll(".khbl-modal__veil:not([data-veil])").forEach(function (v) {
      v.dataset.veil = "1";
      v.addEventListener("click", v.closest("#kh-cat-root") ? window.khblKhCatDong : window.closeKhblModal);
    });
  }
  /* ✂ tách thẻ CCCD: popup LOADING hiện NGAY khi bấm (server 1–12 s), nút đã hx-disabled-elt nên không bấm trùng.
     rootId: "modal-root" (trang Thâu vào) hay "kh-cat-root" (popup khách, lồng). Trước 10/09 nằm inline ở thau.html. */
  window.khblCatLoading = function (label, rootId) {
    var mr = document.getElementById(rootId || "modal-root");
    if (!mr) return;
    var h = document.createElement("h2"); h.textContent = "✂ Đang tách thẻ" + (label ? " " + label : "") + "…";
    mr.innerHTML = '<div class="khbl-modal" role="dialog" aria-modal="true"><div class="khbl-modal__veil" data-veil="1"></div>' +
      '<div class="khbl-modal__box" style="width:min(420px,92vw)"><div class="khbl-modal__head"></div>' +
      '<div class="khbl-modal__body th-cat__cho"><span class="th-cat__quay"></span><div><b>Đang tìm và tách thẻ CCCD</b><br>' +
      '<span class="dim">Mất vài giây (ảnh nhiều chi tiết tới ~10 s). Vui lòng chờ, không bấm lại.</span></div></div></div></div>';
    mr.querySelector(".khbl-modal__head").appendChild(h);
  };
  /* ✓ DÙNG thẻ đã tách (popup khách): server trả JSON {mat, ten, b64, w, h} → dựng File JPEG đặt vào ô tệp của biểu mẫu
     (DataTransfer; trình duyệt không cho gán thì sự kiện formdata của bindAnh vẫn gửi _capturedFile) → xem trước + ghi chú;
     ảnh chỉ lên KK khi bấm LƯU KHÁCH. Lỗi (410 hết hạn / 422 không tách được) → báo đỏ ngay trong popup, không alert. */
  window.khblKhCatNhan = function (e) {
    var xhr = e && e.detail && e.detail.xhr, kq = null;
    try { kq = JSON.parse(xhr.responseText); } catch (_) {}
    if (!xhr || xhr.status !== 200 || !kq || kq.loi || !kq.b64) {
      var body = document.querySelector("#kh-cat-root .khbl-modal__body");
      if (body) {
        var cu = body.querySelector("[data-cat-loi]"); if (cu) cu.remove();
        var d = document.createElement("div"); d.className = "khbl-alert khbl-alert--do"; d.dataset.catLoi = "1"; d.style.marginBottom = "10px";
        d.textContent = (kq && kq.loi) || ("Không nhận được thẻ đã tách (" + (xhr ? xhr.status : "?") + ") — bấm ✂ lại.");
        body.prepend(d);
      }
      return;
    }
    var inp = document.querySelector("#kh-form [name='anh_" + kq.mat + "']");
    if (!inp) return;
    var bin = atob(kq.b64), arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    var file = new File([arr], kq.ten, { type: "image/jpeg", lastModified: Date.now() });
    inp._capturedFile = file;
    try { var dt = new DataTransfer(); dt.items.add(file); inp.files = dt.files; } catch (_) { /* formdata của bindAnh bổ sung */ }
    inp.dispatchEvent(new CustomEvent("khbl:anh-dat", { bubbles: true, detail: { file: file, ghi_chu: "✂ đã tách " + kq.w + "×" + kq.h + " — bấm LƯU KHÁCH để ghi" } }));
    window.khblKhCatDong();
  };

  /* ---------- lưu khách: trạng thái gửi, đóng popup, tải lại danh sách, toast ---------- */
  function customerFormFromEvent(e) {
    var el = e.detail && e.detail.elt;
    if (!el) return null;
    if (el.id === "kh-form") return el;
    return el.closest ? el.closest("#kh-form") : null;
  }
  document.addEventListener("htmx:beforeRequest", function (e) {
    var form = customerFormFromEvent(e);
    if (!form) return;
    form.setAttribute("aria-busy", "true");
    var label = document.querySelector("#kh-save-btn [data-save-label]");
    if (label) label.textContent = "ĐANG LƯU…";
  });
  document.addEventListener("htmx:afterRequest", function (e) {
    var form = customerFormFromEvent(e);
    if (!form) return;
    form.removeAttribute("aria-busy");
    var label = document.querySelector("#kh-save-btn [data-save-label]");
    if (label) label.textContent = "LƯU KHÁCH";
  });
  /* Toast TỰ ẨN (GĐ chốt 08/09/2026): lỗi 60s · cảnh báo 8s · thành công 5s. Áp cho CẢ toast do server
     render qua OOB #toast-root (trước đây đứng mãi tới lần swap kế) lẫn toast JS tạo. Bấm vào toast = đóng ngay. */
  function hanToast(item) {
    if (!item || item.dataset.toast) return;
    item.dataset.toast = "1";
    var ms = item.classList.contains("error") ? 60000 : item.classList.contains("warning") ? 8000 : 5000;
    item.title = "Bấm để đóng";
    item.addEventListener("click", function () { item.remove(); });
    setTimeout(function () { if (item.parentNode) { item.style.transition = "opacity .4s"; item.style.opacity = "0"; setTimeout(function () { item.remove(); }, 420); } }, ms);
  }
  function bindToast(root) {
    var r = document.getElementById("toast-root");
    if (!r) return;
    Array.prototype.forEach.call(r.children, hanToast);
  }
  function showToast(text, tone) {
    var root = document.getElementById("toast-root");
    if (!root || !text) return;
    var item = document.createElement("div");
    item.className = tone || "success";
    item.setAttribute("role", "status");
    item.textContent = text;
    root.appendChild(item);
    hanToast(item);
  }

  /* IN THẲNG Giấy đảm bảo (THANH TOÁN & IN — sửa 08/09/2026 chiều): server nhét sẵn tờ GĐB vào #pos-in (OOB) và gọi
     hàm này → chờ ảnh (mã vạch/QR) nạp xong → đếm lần in (ban_in_dem?im=1) → window.print() TỪ CHÍNH CỬA SỔ NÀY
     (CSS in ẩn shell, chỉ #pos-in hiện) → afterprint dọn #pos-in. Cách cũ nạp iframe ẩn rồi print() trong iframe:
     log Caddy máy quầy cho thấy iframe nạp xong, in/dem đã đếm nhưng Edge 152 BỎ QUA print() của iframe → không ra giấy. */
  window.khblInThang = function (trn, demUrl) {
    var box = document.getElementById("pos-in");
    if (!box || !box.querySelector(".gdb-a5")) return;
    var cho = Array.prototype.map.call(box.querySelectorAll("img"), function (i) {
      return (i.complete && i.naturalWidth) ? Promise.resolve() : new Promise(function (ok) { i.onload = i.onerror = ok; });
    });
    function xong() { box.innerHTML = ""; window.removeEventListener("afterprint", xong); }
    window.addEventListener("afterprint", xong);
    Promise.all(cho).then(function () { return new Promise(function (ok) { setTimeout(ok, 60); }); }).then(function () {
      if (demUrl && window.htmx) {
        window.htmx.ajax("POST", demUrl, { values: { trn_id: trn }, swap: "none",
          headers: { "X-CSRFToken": (window.KHBL_CSRF || "") } });
      }
      window.print();
    });
    setTimeout(function () { if (box.querySelector(".gdb-a5")) xong(); }, 120000);
  };

  /* THÂU VÀO (08/09/2026): chọn khách/NV từ gợi ý → điền hidden + ô text, đóng hộp gợi ý. */
  window.khblThauChon = function (kind, id, ten) {
    var hid = document.getElementById(kind === "nv" ? "th-emp-id" : "th-cust-id");
    var o = document.getElementById(kind === "nv" ? "th-nv" : "th-khach");
    if (hid) hid.value = id;
    if (o) o.value = ten;
    document.querySelectorAll(".th-goiy").forEach(function (b) { b.innerHTML = ""; });
  };
  /* In THẲNG phiếu thâu 110mm: server nhét .th-phieu vào #pos-in (OOB) → chờ logo nạp → window.print() → afterprint dọn. */
  window.khblInThau = function () {
    var box = document.getElementById("pos-in");
    if (!box || !box.querySelector(".th-phieu")) return;
    var cho = Array.prototype.map.call(box.querySelectorAll("img"), function (i) {
      return (i.complete && i.naturalWidth) ? Promise.resolve() : new Promise(function (ok) { i.onload = i.onerror = ok; });
    });
    function xong() { box.innerHTML = ""; window.removeEventListener("afterprint", xong); }
    window.addEventListener("afterprint", xong);
    Promise.all(cho).then(function () { return new Promise(function (ok) { setTimeout(ok, 60); }); }).then(function () { window.print(); });
    setTimeout(function () { if (box.querySelector(".th-phieu")) xong(); }, 120000);
  };

  /* FOCUS ô đang thiếu/lỗi + hiệu ứng rung & viền đỏ 3s (server gửi selector qua _ban_oob loi_o) */
  window.khblFocusLoi = function (sel, _lan2) {
    // ⚠ htmx SETTLE (20ms sau swap) chép lại attribute class từ HTML server → class thêm sớm bị xóa.
    // Chạy trễ 80ms để hiệu ứng sống sau settle.
    if (!_lan2) { setTimeout(function () { window.khblFocusLoi(sel, true); }, 80); return; }
    var el = null;
    try { el = document.querySelector(sel); } catch (_) { return; }
    if (!el) return;
    var box = el.closest(".pg-f, .pg-scan, .pg-doi-nhap, label") || el;
    [el, box].forEach(function (x) { x.classList.remove("khbl-loi-nhay"); });
    // reflow để animation chạy lại khi lỗi lặp
    void el.offsetWidth;
    [el, box].forEach(function (x) { x.classList.add("khbl-loi-nhay"); });
    try { el.focus({ preventScroll: false }); el.select && el.select(); } catch (_) {}
    if (el.scrollIntoView) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setTimeout(function () { [el, box].forEach(function (x) { x.classList.remove("khbl-loi-nhay"); }); }, 3200);
  };
  document.addEventListener("khachSaved", function (e) {
    var detail = e.detail || {};
    window.closeKhblModal();
    var filter = document.querySelector("form.kh-loc");
    if (filter && window.htmx) window.htmx.trigger(filter, "submit");
    showToast(detail.message || "Đã lưu khách hàng", "success");
    (detail.warnings || []).forEach(function (warning) { showToast(warning, "warning"); });
  });
  document.addEventListener("customerSyncDone", function (e) {
    showToast(e.detail.message, e.detail.failed ? "warning" : "success");
  });

  /* Xóa khách xong: đóng popup, tải lại danh sách GIỮ bộ lọc (không reload trang), toast. */
  document.addEventListener("khachDeleted", function (e) {
    var detail = e.detail || {};
    window.closeKhblModal();
    var filter = document.querySelector("form.kh-loc");
    if (filter && window.htmx) window.htmx.trigger(filter, "submit");
    showToast(detail.message || "Đã xóa khách hàng", "success");
  });

  /* Popup xác nhận dùng chung: khóa double-submit, hiển thị tải rồi làm mới dữ liệu. */
  function confirmFormFromEvent(e) { var el = e.detail && e.detail.elt; return el && el.closest ? el.closest(".khbl-confirm__form") : null; }
  document.addEventListener("htmx:beforeRequest", function (e) { var form = confirmFormFromEvent(e); if (!form) return; form.setAttribute("aria-busy", "true"); var b = form.querySelector("button[type='submit']"); if (b) b.disabled = true; });
  document.addEventListener("htmx:afterRequest", function (e) { var form = confirmFormFromEvent(e); if (!form) return; form.removeAttribute("aria-busy"); var b = form.querySelector("button[type='submit']"); if (b) b.disabled = false; });
  document.addEventListener("khblConfirmDone", function (e) { window.closeKhblModal(); showToast((e.detail || {}).message || "Đã hoàn tất thao tác", "success"); window.setTimeout(function () { window.location.reload(); }, 150); });

  /* ---------- bảng giá: không cướp DOM khi đang gõ ---------- */
  var pendingGia = null;
  document.addEventListener("htmx:beforeSwap", function (e) {
    var t = e.detail.target;
    if (t && t.id === "gia-strip") {
      var a = document.activeElement;
      if (a && (a.matches("input,select,textarea") || a.closest(".khbl-scan"))) {
        pendingGia = e.detail.serverResponse;
        e.detail.shouldSwap = false;
      }
    }
  });
  document.addEventListener("focusout", function () {
    if (!pendingGia) return;
    var a = document.activeElement;
    if (a && a.matches("input,select,textarea")) return;
    var strip = document.getElementById("gia-strip");
    if (strip && window.htmx) window.htmx.swap(strip, pendingGia, { swapStyle: "outerHTML" });
    pendingGia = null;
  });

  /* ---------- quét QR CCCD: Enter kết thúc lượt quét; hết giờ chỉ xem trước ---------- */
  function bindQR(root) {
    var el = root.querySelector ? root.querySelector("#kh-qr-in") : null;
    if (!el || el.dataset.qr) return;
    el.dataset.qr = "1";
    var form = el.closest("form"), modal = el.closest(".khbl-modal");
    var msg = form.querySelector("#kh-qr-msg"), preview = form.querySelector("#kh-qr-preview");
    var btn = form.querySelector("#kh-qr-btn"), discard = form.querySelector("#kh-qr-discard");
    var more = form.querySelector("#kh-qr-more");
    var save = modal.querySelector('button[type="submit"][form="kh-form"]');
    var fields = [["f-ten", "ho_ten", "Họ tên"], ["f-cccd", "cmnd", "CCCD"],
      ["f-sinh", "ngay_sinh", "Ngày sinh"], ["f-gt", "gioi_tinh", "Giới tính"],
      ["f-dc", "dia_chi", "Địa chỉ"], ["f-cap", "ngay_cap", "Ngày cấp"]];
    var t = null, pending = null, unresolved = false, lastRaw = "", lastValues = "";
    var requestSerial = 0, scanGroup = [], compareMode = false;
    if (window.addEventListener) {
      var learnedChanged = function (event) {
        if (event.key !== 'khbl-qr-learning-revision') return;
        if (!el.isConnected) { window.removeEventListener('storage', learnedChanged); return; }
        requestSerial++; lastRaw = ''; lastValues = ''; stopTimer(); clearPreview();
        block(!!el.value.trim());
        message('Bộ mẫu QR vừa được cập nhật. Bấm Điền vào biểu mẫu để phân tích lại theo mẫu mới.', 'warn');
      };
      window.addEventListener('storage', learnedChanged);
    }
    function values() { return JSON.stringify(fields.map(function (f) { return form.querySelector("#" + f[0]).value; })); }
    function stopTimer() { if (t !== null) clearTimeout(t); t = null; }
    function block(value) { unresolved = value; if (save) save.disabled = value; discard.hidden = !value; }
    function message(text, tone) { msg.textContent = text; msg.style.color = "var(--" + tone + "-500)"; }
    function clearPreview() { pending = null; preview.replaceChildren(); btn.textContent = "Điền vào biểu mẫu"; }
    function display(key, value) {
      if (key === "gioi_tinh") return value === "1" ? "Nam" : (value === "0" ? "Nữ" : "Chưa xác định");
      if (key === "ngay_sinh" || key === "ngay_cap") return value ? value.split("-").reverse().join("/") : "Để trống";
      return value || "Để trống";
    }
    function apply(d, raw) {
      clearPreview();
      // Ghi cả giá trị trống, tránh giữ thông tin của thẻ quét trước.
      fields.forEach(function (f) { form.querySelector("#" + f[0]).value = d[f[1]]; });
      lastRaw = raw; lastValues = values(); block(false);
      message("Đã điền thông tin từ thẻ. " + (d.canh_bao.length ? "Cần kiểm tra: " + d.canh_bao.join(" · ") : "Kiểm tra lại trước khi lưu khách."),
        d.canh_bao.length ? "warn" : "jade");
    }
    function showPreview(r, raw) {
      clearPreview();
      var table = document.createElement("table"); table.className = "kh-qr-review";
      function row(items, header) {
        var tr = document.createElement("tr");
        items.forEach(function (value) { var cell = document.createElement(header ? "th" : "td"); cell.textContent = value; tr.appendChild(cell); });
        table.appendChild(tr); return tr;
      }
      row(["Thông tin", "Đang có", "Từ thẻ mới"], true);
      var proposed = Object.assign({}, r.data);
      fields.forEach(function (f) {
        var tr = row([f[2], display(f[1], form.querySelector("#" + f[0]).value), display(f[1], proposed[f[1]])]);
        var choices = r.suggestions && r.suggestions[f[1]];
        if (choices && choices.length > 1 && (f[1] === "ho_ten" || f[1] === "dia_chi")) {
          var cell = tr.children[2], select = document.createElement("select");
          select.className = "khbl-input";
          select.setAttribute("aria-label", "Gợi ý " + f[2]);
          choices.forEach(function (c, index) {
            var option = document.createElement("option"); option.value = c.value;
            option.textContent = (index === 0 ? "Ưu tiên: " : "Khả năng khác: ") + c.value;
            select.appendChild(option);
          });
          select.value = proposed[f[1]];
          select.addEventListener("change", function () { proposed[f[1]] = select.value; });
          cell.textContent = ""; cell.appendChild(select);
        }
      });
      preview.appendChild(table);
      if (r.profiles) {
        var explanation = document.createElement("p");
        explanation.textContent = "Nhận diện: " + r.profiles.map(function (p) {
          return "lượt " + p.scan + " — " + p.labels.join(", ");
        }).join("; ") + ". Gợi ý ưu tiên được xếp theo bằng chứng giải mã và đối chiếu, không phải tỷ lệ chính xác.";
        preview.appendChild(explanation);
        var details = document.createElement("details"), summary = document.createElement("summary"), rawText = document.createElement("pre");
        summary.textContent = "Xem dữ liệu quét gốc"; rawText.textContent = (r.raw_scans || [raw]).join("\n\n");
        rawText.style.whiteSpace = "pre-wrap"; rawText.style.wordBreak = "break-all";
        details.appendChild(summary); details.appendChild(rawText); preview.appendChild(details);
      }
      pending = { data: proposed, raw: raw, values: values() };
      btn.textContent = "Áp dụng thông tin đã kiểm tra";
      message((r.thieu.length ? "Đọc được một phần. " : "Đã đọc thẻ, chưa thay đổi biểu mẫu. ") +
        "Đối chiếu thông tin bên dưới; ô trống sẽ xóa giá trị đang có. " + r.canh_bao.join(" · "), "warn");
    }
    function process(final) {
      stopTimer();
      if (!el.isConnected) return;
      var raw = el.value.trim();
      if (raw === lastRaw && values() === lastValues) { block(false); return; }
      block(true);
      if (!window.CCCD || !window.VNText) { message("Chưa tải được bộ đọc thẻ. Tải lại trang rồi thử lại.", "flame"); return; }
      function accept(r) {
        if (!r.data) { clearPreview(); message(r.loi.join(" "), "flame"); return; }
        if (r.raw_scans) scanGroup = r.raw_scans;
        if (more) more.hidden = false;
        var conflict = fields.some(function (f) { var old = form.querySelector("#" + f[0]).value; return old && old !== r.data[f[1]]; });
        if (final && !conflict && !r.canh_bao.length) apply(r.data, raw);
        else showPreview(r, raw);
      }
      if (!el.dataset.analyzeUrl) { accept(window.CCCD.parse(raw)); return; }
      var serial = ++requestSerial, before = values();
      var scans = compareMode ? scanGroup.concat([raw]).filter(function (s, i, a) { return a.indexOf(s) === i; }) : [raw];
      var csrf = form.querySelector('[name="csrfmiddlewaretoken"]');
      message("Đang phân tích dữ liệu quét và xếp hạng gợi ý…", "warn");
      window.CCCD.analyze(el.dataset.analyzeUrl, scans, csrf ? csrf.value : "").then(function (r) {
        if (serial !== requestSerial || !el.isConnected || el.value.trim() !== raw) return;
        if (before !== values()) { clearPreview(); message("Biểu mẫu vừa thay đổi. Bấm phân tích lại để đối chiếu.", "warn"); return; }
        accept(r);
      }).catch(function (err) {
        if (serial !== requestSerial || !el.isConnected) return;
        clearPreview(); message(err.message, "flame");
      });
    }
    el.addEventListener("input", function (e) {
      requestSerial++;
      stopTimer(); clearPreview(); block(!!el.value.trim());
      message(el.value.trim() ? "Đang nhận dữ liệu. Quét xong hãy nhấn Enter hoặc bấm Điền vào biểu mẫu." : "", "warn");
      if (e.isComposing) return;
      // Framing belongs to the server: separators may be missing/repeated and
      // extension fields may follow the issue date. Idle processing only previews.
      if (/^[0-9]{12}/.test(el.value.trim()) && /[0-9]{8}/.test(el.value.trim().slice(12)))
        t = setTimeout(function () { process(false); }, 400);
    });
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault(); e.stopPropagation(); process(true);
        if (!unresolved) el.select();
      }
    });
    btn.addEventListener("click", function () {
      stopTimer();
      if (pending && pending.raw === el.value.trim() && pending.values === values()) apply(pending.data, pending.raw);
      else process(true);
    });
    discard.addEventListener("click", function () {
      requestSerial++; scanGroup = []; compareMode = false;
      if (more) more.hidden = true;
      stopTimer(); clearPreview(); el.value = ""; block(false);
      message("Đã bỏ kết quả quét; biểu mẫu giữ nguyên.", "warn"); el.focus();
    });
    if (more) more.addEventListener("click", function () {
      requestSerial++; stopTimer(); clearPreview(); compareMode = true;
      el.value = ""; block(true); el.focus();
      message("Quét thêm đúng thẻ này bằng máy khác. Hệ thống sẽ đối chiếu các lượt quét.", "warn");
    });
    form.addEventListener("submit", function (e) {
      if (unresolved) { e.preventDefault(); e.stopImmediatePropagation(); message("Hãy áp dụng hoặc bỏ kết quả quét trước khi lưu.", "flame"); }
    }, true);
    form.addEventListener("input", function (e) {
      if (e.target !== el && pending && fields.some(function (f) { return e.target === form.querySelector("#" + f[0]); })) {
        clearPreview(); message("Biểu mẫu vừa thay đổi. Bấm Điền vào biểu mẫu để đối chiếu lại thẻ.", "warn");
      }
    });
    el.focus();
  }

  /* ---------- chọn tệp / chụp ảnh cho ba ảnh khách hàng ---------- */
  function bindAnh(root) {
    var area = root.querySelector ? root.querySelector(".kh-anh") : null;
    if (!area || area.dataset.anh) return;
    area.dataset.anh = "1";
    var form = area.closest("form"), dialog = form && form.querySelector("#kh-camera");
    if (!dialog) return;
    var video = dialog.querySelector("[data-camera-video]");
    var canvas = dialog.querySelector("[data-camera-canvas]");
    var shot = dialog.querySelector("[data-camera-preview]");
    var title = dialog.querySelector("#kh-camera-title");
    var msg = dialog.querySelector("[data-camera-msg]");
    var capture = dialog.querySelector("[data-camera-capture]");
    var retake = dialog.querySelector("[data-camera-retake]");
    var use = dialog.querySelector("[data-camera-use]");
    var stream = null, target = null, blob = null, shotUrl = "";

    function stopStream() {
      if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
      if (video) video.srcObject = null;
    }
    function revokeShot() {
      if (shotUrl) URL.revokeObjectURL(shotUrl);
      shotUrl = "";
    }
    function closeCamera() {
      stopStream(); revokeShot(); blob = null; target = null;
      if (dialog.open) dialog.close();
    }
    window.__khblStopCamera = closeCamera;

    function showFile(inp, file) {
      var card = inp.closest("[data-photo]"), box = card && card.querySelector("[data-xem]");
      if (!box || !file) return;
      if (box.dataset.url) URL.revokeObjectURL(box.dataset.url);
      var url = URL.createObjectURL(file), img = document.createElement("img");
      box.dataset.url = url;
      img.alt = "Xem trước " + (card.dataset.label || "ảnh").toLowerCase();
      img.src = url; box.replaceChildren(img);
    }
    function ghiChu(inp, text) {
      var card = inp.closest("[data-photo]"), tt = card && card.querySelector("[data-cat-tt]");
      if (tt) tt.textContent = text || "";
    }
    area.querySelectorAll("[data-photo-input]").forEach(function (inp) {
      inp.dataset.anh = "1";
      inp.addEventListener("change", function () {
        var f = inp.files && inp.files[0];
        if (!f) return;
        if (!inp._capturedFile || inp._capturedFile.name !== f.name) { inp._capturedFile = null; ghiChu(inp, ""); }
        showFile(inp, f);
      });
    });
    // ✂ thẻ đã tách (khblKhCatNhan) đặt tệp bằng code → không có 'change' → nhận qua sự kiện riêng: xem trước + ghi chú
    area.addEventListener("khbl:anh-dat", function (e) {
      var inp = e.target, f = e.detail && e.detail.file;
      if (!f || !inp.matches || !inp.matches("[data-photo-input]")) return;
      showFile(inp, f); ghiChu(inp, e.detail.ghi_chu);
    });

    // Dự phòng cho trình duyệt không cho gán FileList: vẫn đưa ảnh chụp vào FormData.
    form.addEventListener("formdata", function (e) {
      area.querySelectorAll("[data-photo-input]").forEach(function (inp) {
        if (inp._capturedFile) e.formData.set(inp.name, inp._capturedFile, inp._capturedFile.name);
      });
    });

    function cameraError(err) {
      var text = "Không mở được camera. Bạn vẫn có thể dùng Chọn tệp.";
      if (err && err.name === "NotAllowedError") text = "Camera chưa được cấp quyền. Hãy cho phép camera rồi thử lại.";
      else if (err && err.name === "NotFoundError") text = "Không tìm thấy camera trên thiết bị.";
      else if (err && err.name === "NotReadableError") text = "Camera đang được ứng dụng khác sử dụng.";
      video.hidden = true;
      msg.textContent = text; msg.style.color = "var(--flame-500)";
      capture.disabled = true;
    }
    async function openStream() {
      stopStream(); revokeShot(); blob = null;
      video.hidden = false; shot.hidden = true;
      capture.hidden = false; capture.disabled = true; retake.hidden = true; use.hidden = true;
      msg.textContent = "Đang mở camera…"; msg.style.color = "var(--ink-600)";
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        cameraError({ name: "NotSupportedError" }); return;
      }
      var wanted = target;
      try {
        var opened = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: wanted.closest("[data-photo]").dataset.facing || "environment" } }
        });
        if (!dialog.open || target !== wanted) {
          opened.getTracks().forEach(function (track) { track.stop(); }); return;
        }
        stream = opened; video.srcObject = stream;
        await video.play();
        capture.disabled = false; msg.textContent = "Đặt ảnh ngay ngắn trong khung rồi bấm Chụp.";
      } catch (err) { cameraError(err); }
    }

    area.querySelectorAll("[data-chup]").forEach(function (button) {
      button.addEventListener("click", function () {
        var card = button.closest("[data-photo]");
        target = card.querySelector("[data-photo-input]");
        title.textContent = "Chụp " + (card.dataset.label || "ảnh").toLowerCase();
        if (typeof dialog.showModal !== "function") { target.click(); return; }
        dialog.showModal(); openStream();
      });
    });
    dialog.querySelectorAll("[data-camera-close]").forEach(function (button) {
      button.addEventListener("click", closeCamera);
    });
    dialog.addEventListener("cancel", function (e) { e.preventDefault(); closeCamera(); });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) closeCamera(); });
    capture.addEventListener("click", function () {
      if (!stream || !video.videoWidth || !video.videoHeight) return;
      var max = 1600, scale = Math.min(1, max / Math.max(video.videoWidth, video.videoHeight));
      canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      stopStream(); capture.disabled = true; msg.textContent = "Đang tạo ảnh…";
      canvas.toBlob(function (result) {
        if (!result || !dialog.open) { cameraError(); return; }
        blob = result; shotUrl = URL.createObjectURL(blob); shot.src = shotUrl;
        video.hidden = true; shot.hidden = false; capture.hidden = true;
        retake.hidden = false; use.hidden = false;
        msg.textContent = "Kiểm tra ảnh, sau đó lưu ảnh hoặc chụp lại.";
      }, "image/jpeg", 0.9);
    });
    retake.addEventListener("click", openStream);
    use.addEventListener("click", function () {
      if (!blob || !target) return;
      var name = target.name + "-" + Date.now() + ".jpg";
      var file = new File([blob], name, { type: "image/jpeg", lastModified: Date.now() });
      target._capturedFile = file;
      try {
        var transfer = new DataTransfer(); transfer.items.add(file); target.files = transfer.files;
      } catch (_) { /* sự kiện formdata phía trên sẽ bổ sung tệp */ }
      var inp = target;                       // GIỮ tham chiếu: closeCamera() đặt target = null → dispatch lên null bị nuốt im (bẫy 09/09)
      showFile(inp, file); closeCamera();
      // 08/09 tối (thâu): form ảnh tự tải lên theo 'change' — gán files bằng code không bắn sự kiện → bắn tay
      try { inp.dispatchEvent(new Event("change", { bubbles: true })); } catch (_) {}
    });
  }


  /* ---------- đồng hồ thời gian thực ở chân trang ---------- */
  var THU = ["Chủ nhật", "Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy"];
  function hai(n) { return n < 10 ? "0" + n : "" + n; }
  function nhipDongHo() {
    var o = document.getElementById("khbl-dongho");
    if (!o) return;
    var g = o.querySelector("[data-gio]"), n = o.querySelector("[data-ngay]"), d = new Date();
    if (g) g.textContent = hai(d.getHours()) + ":" + hai(d.getMinutes()) + ":" + hai(d.getSeconds());
    if (n) n.textContent = THU[d.getDay()] + ", " + hai(d.getDate()) + "/" +
                           hai(d.getMonth() + 1) + "/" + d.getFullYear();
  }
  function batDongHo() {
    if (window.__khblDongHo) return;          // chỉ 1 nhịp cho cả phiên
    nhipDongHo();
    window.__khblDongHo = setInterval(nhipDongHo, 1000);
  }

  /* ---------- cảnh báo MySQL local khác PMV Report: server cache + nhịp 2 phút ---------- */
  function batCanhBaoGia() {
    var alert = document.getElementById("khbl-price-alert");
    if (!alert || alert.dataset.bound) return;
    alert.dataset.bound = "1";
    var message = alert.querySelector("[data-price-alert-text]");
    function check() {
      fetch(alert.dataset.url, { headers: { "X-Requested-With": "XMLHttpRequest" }, cache: "no-store" })
        .then(function (response) { if (!response.ok) throw new Error("status"); return response.json(); })
        .then(function (data) {
          var count = Number(data.mismatch_count || 0);
          if (data.ok && count > 0) {
            if (message) message.textContent = "";
            alert.hidden = false;
            document.documentElement.classList.add("khbl-gia-lech");
          } else {
            alert.hidden = true;
            document.documentElement.classList.remove("khbl-gia-lech");
          }
        })
        .catch(function () { alert.hidden = true; document.documentElement.classList.remove("khbl-gia-lech"); });
    }
    check();
    window.setInterval(check, 2 * 60 * 1000);
  }

  /* ---------- điểm vào ---------- */
  window.khblBind = function (root) {
    root = root || document;
    if (!root.querySelectorAll) return;
    bindMoney(root);
    bindScan(root);
    bindModal(root);
    bindQR(root);
    bindAnh(root);
    bindToast(root);
    // KHÔNG cướp focus khi vừa có lỗi cần chú ý (khblFocusLoi đã đặt focus vào ô thiếu)
    var f = root.querySelector("[data-autofocus]");
    if (f && !document.querySelector(".khbl-loi-nhay")) { try { f.focus(); f.select && f.select(); } catch (_) {} }
  };
  document.addEventListener("DOMContentLoaded", function () { window.khblBind(document); batDongHo(); batCanhBaoGia(); });
  document.addEventListener("htmx:afterSwap", function (e) { window.khblBind(e.target); });
  document.addEventListener("htmx:afterSettle", function (e) { window.khblBind(e.target); });
})();

/* ---------- GIỮ Ô ĐANG GÕ qua HTMX swap (dùng chung mọi trang — 09/09/2026, thay 2 bản inline thau.html/ban.html) ----------
   Vấn đề cũ: bản inline chạy cho CẢ yêu cầu lọc gợi ý (ô Khách tự gửi keyup) rồi sau phản hồi đặt lại con trỏ về vị trí LÚC GỬI
   → chữ gõ tiếp bị chèn vào giữa, ô "giật", sai chữ. Quy tắc mới:
   · ô đang gõ chính là NGUỒN gửi (gợi ý / lọc) → không đụng gì cả;
   · chụp giá trị + con trỏ lúc SẮP SWAP (ô cũ còn trong DOM) → giữ được cả chữ gõ trong lúc chờ phản hồi;
   · sau settle, ô vẫn là chính nó và còn focus → không đụng; ô bị THAY (OOB thay trọn khối) → tìm ô mới (id → name trong form
     cùng class → name trong #modal-root), trả chữ (trừ khi chính ô đó vừa gửi — giữ giá trị server đã định dạng), focus, con trỏ. */
(function () {
  var giu = null;
  document.addEventListener("htmx:beforeRequest", function (e) {
    var a = document.activeElement;
    if (!a || !a.matches || !a.matches("input, select, textarea")) { giu = null; return; }
    var src = e.detail && e.detail.elt;
    if (src === a) { giu = null; return; }                       // ô gợi ý / lọc tự gửi → để yên
    var ev = e.detail && e.detail.requestConfig && e.detail.requestConfig.triggeringEvent;
    giu = { el: a, id: a.id, name: a.name, form: a.closest("form") && a.closest("form").className, value: a.value, pos: a.selectionStart, end: a.selectionEnd,
            la_nguon: !!(src && src.contains && src.contains(a) && ev && ev.target === a) };
  });
  document.addEventListener("htmx:beforeSwap", function () {
    if (!giu || !giu.el || !document.contains(giu.el)) return;
    giu.value = giu.el.value; giu.pos = giu.el.selectionStart; giu.end = giu.el.selectionEnd;
  });
  document.addEventListener("htmx:afterSettle", function () {
    if (!giu) return;
    var g = giu; giu = null;
    if (document.contains(g.el) && document.activeElement === g.el) return;   // không bị thay → không đụng
    var el = g.id ? document.getElementById(g.id) : null;
    if (!el && g.name && g.form) { var f = document.querySelector("form." + g.form.split(" ")[0]); el = f && f.querySelector("[name='" + g.name + "']"); }
    if (!el && g.name) { var mr = document.getElementById("modal-root"); el = mr && mr.querySelector("[name='" + g.name + "']"); }
    if (!el || el.disabled || el === g.el) return;
    if (!g.la_nguon && g.value != null && g.value !== "" && el.value !== g.value && el.tagName === "INPUT" && el.type !== "date") el.value = g.value;
    try {
      el.focus({ preventScroll: true });
      if (g.pos != null && el.setSelectionRange && /^(text|search|tel|url|password)$/.test(el.type || "text")) {
        var n = el.value.length, p = Math.min(g.pos, n), q = Math.min(g.end != null ? g.end : g.pos, n);
        el.setSelectionRange(p, q);
      }
    } catch (_) {}
  });
  window.khblGiuO = true;
})();

/* Ba số điện thoại: phản hồi sớm, server kiểm lại trong khóa trước khi ghi. */
(function () {
  let timer, generation = 0;
  document.addEventListener("input", function (event) {
    const input = event.target;
    if (!input.matches || !input.matches("#kh-form [name=Phone], #kh-form [name=GhiChu2], #kh-form [name=GhiChu3]")) return;
    const form = input.closest("form"), status = form.querySelector("#kh-phone-status");
    const fields = ["Phone", "GhiChu2", "GhiChu3"].map(name => form.elements[name]);
    const version = ++generation;
    clearTimeout(timer);
    fields.forEach(field => field.setCustomValidity(""));
    const filled = fields.filter(field => field.value);
    if (filled.some(field => !/^[0-9]{10}$/.test(field.value))) {
      status.textContent = "Số điện thoại phải gồm đúng 10 chữ số."; return;
    }
    if (new Set(filled.map(field => field.value)).size !== filled.length) {
      input.setCustomValidity("Ba số điện thoại phải khác nhau");
      status.textContent = "Ba số điện thoại phải khác nhau."; return;
    }
    if (!filled.length) { status.textContent = "Có thể nhập tối đa 3 số điện thoại khác nhau."; return; }
    status.textContent = "Đang kiểm tra số điện thoại…";
    timer = setTimeout(async function () {
      try {
        const results = await Promise.all(filled.map(async function (field) {
          const query = new URLSearchParams({phone: field.value, cust_id: form.elements.CustID.value});
          const response = await fetch(form.dataset.phoneCheck + "?" + query, {cache: "no-store"});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Chưa kiểm tra được SĐT.");
          return {field, matches: data.matches};
        }));
        if (version !== generation || !form.isConnected) return;
        const messages = [];
        results.forEach(function ({field, matches}) {
          if (!matches.length) return;
          const message = field.value + " đã thuộc " + matches.map(row => (row.CustCode || row.CustID) + " — " + row.CustName).join("; ");
          field.setCustomValidity(message); messages.push(message);
        });
        status.textContent = messages.length ? messages.join(". ") : "Các số điện thoại chưa thuộc khách khác. Hệ thống sẽ kiểm tra lại khi lưu.";
      } catch (error) {
        if (version === generation && form.isConnected) status.textContent = error.message;
      }
    }, 300);
  });
})();
