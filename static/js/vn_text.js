/* CHUẨN HÓA CHỮ TIẾNG VIỆT BỊ HỎNG MÃ — bản JS, cùng thuật toán với apps/pos/vn_text.py.
   Tài liệu: .claude/skills/chuan-hoa-tieng-viet/
   Dùng: var r = VNText.chuanHoa(s);  // {text, canhBao:[...]}                              */
(function (g) {
  "use strict";

  var CHU_VIET = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ";
  var KY_TU_RAC = "ÃÂÄÅÆÐÑ×÷ØÞßÝ¡¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ�";
  var HONG = "�";
  /* CP1252 0x80–0x9F ↔ ký tự Unicode — cần để đảo ngược mojibake */
  var CP1252 = { 0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85,
    0x2020: 0x86, 0x2021: 0x87, 0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A, 0x2039: 0x8B,
    0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91, 0x2019: 0x92, 0x201C: 0x93, 0x201D: 0x94,
    0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9A,
    0x203A: 0x9B, 0x0153: 0x9C, 0x017E: 0x9E, 0x0178: 0x9F };
  var NGUYEN_AM = "aăâeêioôơuưy", PHU_AM = "bcdđghklmnpqrstvx";

  function diemViet(s) {
    var d = 0, i, c;
    for (i = 0; i < s.length; i++) {
      c = s[i];
      if (CHU_VIET.indexOf(c.toLowerCase()) >= 0) d++;
      if (KY_TU_RAC.indexOf(c) >= 0) d--;
    }
    return d;
  }
  function deUTF8(bytes, thay) {
    try { return new TextDecoder("utf-8", { fatal: !thay }).decode(new Uint8Array(bytes)); }
    catch (e) { return null; }
  }

  /* ① byte in ra thành số: '0225''0187''0141' → E1 BB 8D → 'ọ' */
  function suaByteSo(s, cb) {
    var re = /(?:0(?:1[2-9]\d|2[0-5]\d))+/g;
    var kq = s.replace(re, function (cum) {
      var bs = [], i;
      for (i = 0; i < cum.length; i += 4) bs.push(parseInt(cum.substr(i + 1, 3), 10));
      return deUTF8(bs, true) || cum;
    });
    if (kq !== s) cb.push("Đã dựng lại chữ từ mã số byte UTF-8");
    return kq;
  }

  /* ② mojibake: byte UTF-8 bị đọc bằng CP1252 → 'TrÆ°Æ¡ng' */
  function suaMojibake(s, cb) {
    var i, cp, bs = [];
    for (i = 0; i < s.length; i++) {
      cp = s.charCodeAt(i);
      if (cp < 0x100) bs.push(cp);
      else if (CP1252[cp] !== undefined) bs.push(CP1252[cp]);
      else return s;                       // có ký tự ngoài CP1252 ⇒ chuỗi vốn đã đúng
    }
    var chat = deUTF8(bs, false), long = deUTF8(bs, true);
    var thu = chat || long;
    if (thu && diemViet(thu) > diemViet(s)) { cb.push("Đã sửa lỗi mã kiểu mojibake"); return thu; }
    return s;
  }

  /* ③ thực thể HTML và %XX */
  function suaThucThe(s, cb) {
    if (s.indexOf("&#") < 0) return s;
    var kq = s.replace(/&#(x[0-9a-fA-F]+|\d+);/g, function (m, v) {
      var n = v[0] === "x" || v[0] === "X" ? parseInt(v.slice(1), 16) : parseInt(v, 10);
      return isNaN(n) ? m : String.fromCodePoint(n);
    });
    if (kq !== s) cb.push("Đã đổi thực thể HTML về chữ");
    return kq;
  }
  function suaPercent(s, cb) {
    if (s.indexOf("%") < 0) return s;
    var kq = s.replace(/(?:%[0-9a-fA-F]{2})+/g, function (m) {
      try { return decodeURIComponent(m); } catch (e) { return m; }
    });
    if (kq !== s) cb.push("Đã giải mã đoạn %XX");
    return kq;
  }

  /* ④ byte bị NUỐT: điền lại KHI VÀ CHỈ KHI còn đúng 1 khả năng hợp lệ */
  var BI_NUOT = [];
  (function () { var b; for (b = 0x82; b <= 0x8C; b++) BI_NUOT.push(b);
                 for (b = 0x91; b <= 0x9C; b++) BI_NUOT.push(b); BI_NUOT.push(0x9E, 0x9F); })();
  function ungVien() {
    var ra = [], dau = [0xC3, 0xC4, 0xC5, 0xC6], i, j, c;
    for (i = 0; i < dau.length; i++) for (j = 0; j < BI_NUOT.length; j++) {
      c = deUTF8([dau[i], BI_NUOT[j]], false);
      if (c && (CHU_VIET.indexOf(c.toLowerCase()) >= 0 || "ăâêôơư".indexOf(c.toLowerCase()) >= 0)) ra.push(c);
    }
    return ra;
  }
  function suaByteMat(s, cb) {
    if (s.indexOf(HONG) < 0) return s;
    var a = s.split(""), doan = [], i, j, truoc, sau, uv;
    for (i = 0; i < a.length; i++) {
      if (a[i] !== HONG) continue;
      truoc = ""; for (j = i - 1; j >= 0; j--) if (a[j] !== HONG) { truoc = a[j]; break; }
      sau = i + 1 < a.length ? a[i + 1] : "";
      uv = ungVien();
      if ((sau && sau === sau.toLowerCase() && sau !== sau.toUpperCase()) ||
          (truoc && truoc === truoc.toLowerCase() && truoc !== truoc.toUpperCase()))
        uv = uv.filter(function (x) { return x === x.toLowerCase(); });
      if (PHU_AM.indexOf(truoc.toLowerCase()) >= 0 && PHU_AM.indexOf(sau.toLowerCase()) >= 0)
        uv = uv.filter(function (x) { return NGUYEN_AM.indexOf(x.toLowerCase()) >= 0; });
      uv = uv.filter(function (v, k, arr) { return arr.indexOf(v) === k; });
      if (uv.length === 1) { a[i] = uv[0]; doan.push(truoc + uv[0] + sau); }
    }
    if (doan.length) cb.push("Đã dựng lại " + doan.length + " ký tự mất byte: " +
      doan.filter(function (v, k, arr) { return arr.indexOf(v) === k; }).join(", ") + " — nhìn lại cho chắc");
    return a.join("");
  }

  function chuanHoa(s) {
    if (!s) return { text: "", canhBao: [] };
    var cb = [], kq = String(s);
    kq = suaThucThe(kq, cb);
    kq = suaPercent(kq, cb);
    kq = suaByteSo(kq, cb);
    kq = suaMojibake(kq, cb);
    kq = suaByteMat(kq, cb);
    if (String.prototype.normalize) kq = kq.normalize("NFC");
    kq = kq.replace(/[ \t]+/g, " ").trim();
    if (kq.indexOf(HONG) >= 0) cb.push("Còn ký tự không khôi phục được — chỗ hiện ‹?› cần gõ tay lại");
    return { text: kq, canhBao: cb };
  }

  function hoaDauTu(s) {
    s = String(s || "").replace(/\s+/g, " ").trim();
    if (!s || s !== s.toUpperCase()) return s;
    return s.split(" ").map(function (w) {
      return w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w; }).join(" ");
  }

  g.VNText = { chuanHoa: chuanHoa, hoaDauTu: hoaDauTu, boDau: function (s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/đ/g, "d").replace(/Đ/g, "D"); } };
})(window);
