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
    var r = document.getElementById("modal-root");
    if (r) r.innerHTML = "";
  };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      var r = document.getElementById("modal-root");
      if (r && r.innerHTML.trim()) { e.stopPropagation(); window.closeKhblModal(); }
    }
  }, true);
  function bindModal(root) {
    root.querySelectorAll(".khbl-modal__veil:not([data-veil])").forEach(function (v) {
      v.dataset.veil = "1";
      v.addEventListener("click", window.closeKhblModal);
    });
  }

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

  /* ---------- quét QR thẻ CCCD ----------
     Khuôn 7 trường: CCCD|CMND cũ|họ tên|ddmmyyyy sinh|giới tính|địa chỉ|ddmmyyyy cấp
     Máy quét gõ như bàn phím nên vừa quét xong là điền luôn, không cần bấm nút. */
  function docCCCD(raw) {
    // CHUẨN HÓA trước: máy quét đi qua clipboard/Excel hay trả chữ hỏng mã
    // ('Tr0198017601980161ng' → 'Trương'). Thuật toán: static/js/vn_text.js
    var ch = window.VNText ? window.VNText.chuanHoa(raw) : { text: String(raw || ""), canhBao: [] };
    var p = ch.text.split("|").map(function (x) { return x.trim(); });
    if (p.length < 6 || !/^\d{9,12}$/.test(p[0])) return null;
    return { cccd: p[0], ten: hoaDauTu(p[2]), sinh: ngayISO(p[3]),
             nam: /^nam/i.test(p[4] || ""), diaChi: p[5], cap: ngayISO(p[6] || ""),
             canhBao: ch.canhBao };
  }
  function ngayISO(s) {
    s = String(s || "").replace(/\D/g, "");
    if (s.length !== 8) return "";
    var d = s.slice(0, 2), m = s.slice(2, 4), y = s.slice(4);
    return y + "-" + m + "-" + d;
  }
  function hoaDauTu(t) {
    return window.VNText ? window.VNText.hoaDauTu(t) : String(t || "").trim();
  }
  function dienCCCD(raw) {
    var msg = document.getElementById("kh-qr-msg");
    var d = docCCCD(raw);
    if (!d) {
      if (msg) msg.innerHTML = '<span style="color:var(--flame-500)">Chuỗi không đúng khuôn thẻ căn cước (cần 7 trường ngăn bằng dấu |).</span>';
      return false;
    }
    var set = function (id, v) { var el = document.getElementById(id); if (el && v) el.value = v; };
    set("f-ten", d.ten); set("f-cccd", d.cccd); set("f-sinh", d.sinh);
    set("f-dc", d.diaChi); set("f-cap", d.cap);
    var gt = document.getElementById("f-gt"); if (gt) gt.value = d.nam ? "1" : "0";
    if (msg) {
      var h = '<span style="color:var(--jade-500)">Đã điền từ thẻ căn cước: <b>' + d.ten +
        '</b> · ' + d.cccd + (d.sinh ? ' · sinh ' + d.sinh.split("-").reverse().join("/") : "") + '</span>';
      (d.canhBao || []).forEach(function (c) {
        h += '<br><span style="color:var(--warn-500)">⚠ ' + c + '</span>';
      });
      msg.innerHTML = h;
    }
    return true;
  }
  function bindQR(root) {
    var el = root.querySelector ? root.querySelector("#kh-qr-in") : null;
    if (!el || el.dataset.qr) return;
    el.dataset.qr = "1";
    var t = null;
    el.addEventListener("input", function () {
      if (t) clearTimeout(t);
      if (el.value.indexOf("|") < 0) return;
      t = setTimeout(function () { if (dienCCCD(el.value)) el.select(); }, 150);
    });
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); dienCCCD(el.value); }
    });
    var btn = root.querySelector("#kh-qr-btn");
    if (btn) btn.addEventListener("click", function () { dienCCCD(el.value); });
  }

  /* ---------- xem trước ảnh chọn ---------- */
  function bindAnh(root) {
    root.querySelectorAll('.kh-anh input[type=file]:not([data-anh])').forEach(function (inp) {
      inp.dataset.anh = "1";
      inp.addEventListener("change", function () {
        var khung = inp.parentElement.querySelector("[data-xem]");
        var f = inp.files && inp.files[0];
        if (!khung || !f) return;
        var url = URL.createObjectURL(f);
        khung.innerHTML = '<img alt="">';
        khung.firstChild.src = url;
      });
    });
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
    var f = root.querySelector("[data-autofocus]");
    if (f) { try { f.focus(); f.select && f.select(); } catch (_) {} }
  };
  document.addEventListener("DOMContentLoaded", function () { window.khblBind(document); });
  document.addEventListener("htmx:afterSwap", function (e) { window.khblBind(e.target); });
  document.addEventListener("htmx:afterSettle", function (e) { window.khblBind(e.target); });
})();
