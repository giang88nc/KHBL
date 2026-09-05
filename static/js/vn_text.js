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
    var re = /(?:0(?:12[89]|1[3-9]\d|2[0-4]\d|25[0-5]))+/g;
    var kq = s.replace(re, function (cum, offset) {
      // Không diễn giải một phần mã số / số nhà thành chữ.
      var before = s[offset - 1] || "", after = s[offset + cum.length] || "";
      if (/\d/.test(before + after)) return cum;
      var bs = [], i;
      for (i = 0; i < cum.length; i += 4) bs.push(parseInt(cum.substr(i + 1, 3), 10));
      var decoded = deUTF8(bs, false);
      var letter = function (c) { return !!c && /\p{L}/u.test(c); };
      if (decoded && Array.from(decoded).every(function (c) {
        return CHU_VIET.indexOf(c.toLowerCase()) >= 0;
      }) && (letter(before) || letter(after) || cum === s)) return decoded;
      // Chỉ phục hồi byte mồ côi ở giữa một từ, ví dụ N0196m.
      if (bs.length === 1 && bs[0] >= 0xC2 && bs[0] <= 0xF4 &&
          letter(before) && letter(after)) return HONG;
      return cum;
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
      return !Number.isInteger(n) || n < 0 || n > 0x10FFFF ||
        (n >= 0xD800 && n <= 0xDFFF) ? m : String.fromCodePoint(n);
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

  /* Mã Unicode thập phân bị máy quét gõ thành chữ số. Một dãy có thể chứa
     nhiều code point nối liền: Tr432417ng = Tr + 432(ư) + 417(ơ) + ng. */
  function suaMaUnicode(s, cb) {
    var allowed = CHU_VIET + "ăâêôơư";
    function tach(digits) {
      if (digits.length > 32) return null;
      var ways = [];
      function tim(pos, out) {
        if (ways.length > 1) return;
        if (pos === digits.length) { ways.push(out); return; }
        [3, 4].forEach(function (size) {
          if (pos + size > digits.length) return;
          var n = Number(digits.slice(pos, pos + size));
          if (!Number.isInteger(n) || n > 0x10FFFF) return;
          var c = String.fromCodePoint(n);
          if (allowed.indexOf(c.toLowerCase()) >= 0) tim(pos + size, out + c);
        });
      }
      tim(0, "");
      return ways.length === 1 ? ways[0] : null;
    }
    var kq = s.replace(/\d{3,}/g, function (digits, offset) {
      var before = s[offset - 1] || "", after = s[offset + digits.length] || "";
      if (!(/\p{L}/u.test(before) || /\p{L}/u.test(after))) return digits;
      var fixed = tach(digits);
      return fixed === null ? digits : fixed;
    });
    if (kq !== s) cb.push("Đã đổi mã Unicode thập phân trong chữ tiếng Việt");
    return kq;
  }

  /* Unicode bị cắt còn byte thấp rồi hiện bằng font DOS/OEM. ≤ và α chỉ được
     sửa trong ngữ cảnh từ đã thấy chắc chắn từ máy quét; không thay toàn cục. */
  function suaOEM(s, cb) {
    var changed = false;
    var kq = s.replace(/\S+/g, function (word) {
      var fixed = word;
      if (/[░═♥]/.test(word)) fixed = word.replace(/░/g, "ư").replace(/═/g, "ọ").replace(/♥/g, "ă");
      if (word.indexOf("░") >= 0) fixed = fixed.replace(/í/g, "ơ");
      // Một số nơi đã đổi byte đầu trước, để lại trạng thái nửa chừng Trưíng.
      fixed = fixed.replace(/Trưíng/g, "Trương").replace(/TRƯÍNG/g, "TRƯƠNG");
      fixed = fixed.replace(/^([Kk]h)≤m(?=\W|$)/u, "$1óm");
      fixed = fixed.replace(/^Cα(?=\W|$)/u, "Cà");
      if (fixed !== word) changed = true;
      return fixed;
    });
    if (changed) cb.push("Đã phục hồi ký tự DOS/OEM từ byte thấp — vui lòng đối chiếu với thẻ");
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

  function suaByteDauMoCoi(s, cb) {
    var a = s.split(""), doan = [], i, lead, truoc, sau, uv;
    for (i = 0; i < a.length; i++) {
      lead = a[i].charCodeAt(0);
      if ([0xC3, 0xC4, 0xC5, 0xC6].indexOf(lead) < 0) continue;
      truoc = i ? a[i - 1] : ""; sau = i + 1 < a.length ? a[i + 1] : "";
      if (!(letter(truoc) && letter(sau))) continue;
      uv = BI_NUOT.map(function (b) { return deUTF8([lead, b], false); })
        .filter(function (x) { return x && (CHU_VIET.indexOf(x.toLowerCase()) >= 0 ||
          "ăâêôơư".indexOf(x.toLowerCase()) >= 0); });
      if ((sau && sau === sau.toLowerCase() && sau !== sau.toUpperCase()) ||
          (truoc && truoc === truoc.toLowerCase() && truoc !== truoc.toUpperCase()))
        uv = uv.filter(function (x) { return x === x.toLowerCase(); });
      else if (truoc === truoc.toUpperCase() && sau === sau.toUpperCase())
        uv = uv.filter(function (x) { return x === x.toUpperCase(); });
      if (PHU_AM.indexOf(truoc.toLowerCase()) >= 0 && PHU_AM.indexOf(sau.toLowerCase()) >= 0)
        uv = uv.filter(function (x) { return NGUYEN_AM.indexOf(x.toLowerCase()) >= 0; });
      uv = uv.filter(function (v, k, arr) { return arr.indexOf(v) === k; });
      if (uv.length === 1) { a[i] = uv[0]; doan.push(truoc + uv[0] + sau); }
    }
    if (doan.length) cb.push("Đã phục hồi byte đầu UTF-8 bị sót: " +
      doan.filter(function (v, k, arr) { return arr.indexOf(v) === k; }).join(", ") +
      " — vui lòng đối chiếu với thẻ");
    return a.join("");
  }

  function chuanHoa(s) {
    if (!s) return { text: "", canhBao: [] };
    var cb = [], kq = String(s);
    kq = suaThucThe(kq, cb);
    kq = suaPercent(kq, cb);
    kq = suaByteSo(kq, cb);
    kq = suaMaUnicode(kq, cb);
    kq = suaOEM(kq, cb);
    kq = suaMojibake(kq, cb);
    kq = suaByteDauMoCoi(kq, cb);
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
