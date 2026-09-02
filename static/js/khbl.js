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

  /* ---------- điểm vào ---------- */
  window.khblBind = function (root) {
    root = root || document;
    if (!root.querySelectorAll) return;
    bindMoney(root);
    bindScan(root);
    bindModal(root);
    var f = root.querySelector("[data-autofocus]");
    if (f) { try { f.focus(); f.select && f.select(); } catch (_) {} }
  };
  document.addEventListener("DOMContentLoaded", function () { window.khblBind(document); });
  document.addEventListener("htmx:afterSwap", function (e) { window.khblBind(e.target); });
  document.addEventListener("htmx:afterSettle", function (e) { window.khblBind(e.target); });
})();
