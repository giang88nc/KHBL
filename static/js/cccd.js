/* Bộ đọc QR thuần dữ liệu; cùng quy tắc với apps/pos/cccd.py. Không sửa DOM / gửi mạng. */
(function (g) {
  "use strict";
  function ngay(s) {
    if (!/^[0-9]{8}$/.test(s || "")) return "";
    var d = Number(s.slice(0, 2)), m = Number(s.slice(2, 4)), y = Number(s.slice(4));
    if (y < 1 || m < 1 || m > 12) return "";
    var days = [31, y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0) ? 29 : 28,
      31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return d >= 1 && d <= days[m - 1] ? s.slice(4) + "-" + s.slice(2, 4) + "-" + s.slice(0, 2) : "";
  }
  function parse(raw) {
    var loi = [], cb = [], thieu = [], p = String(raw || "").trim().split("|").map(function (v) { return v.trim(); });
    var result = { data: null, loi: loi, canh_bao: cb, thieu: thieu };
    if (String(raw || "").length > 8192 || p.length !== 7) {
      loi.push("Chuỗi quét cần đúng 7 trường ngăn bằng dấu |. Hãy quét lại đầy đủ."); return result;
    }
    if (!/^[0-9]{12}$/.test(p[0])) loi.push("Số CCCD trong QR phải gồm đúng 12 chữ số.");
    if (p[1] && !/^[0-9]{9}$/.test(p[1])) loi.push("Số CMND cũ phải gồm 9 chữ số hoặc để trống.");
    function text(value, label) {
      var r = g.VNText.chuanHoa(value);
      r.canhBao.forEach(function (c) { cb.push(label + ": " + c); });
      if (/[\u0000-\u001f\u007f<>|]/.test(r.text)) loi.push(label + " chứa ký tự không hợp lệ.");
      if (/�|\?|&#|%[0-9a-f]{2}|0(?:19[5-9]|2[0-4][0-9])/i.test(r.text))
        cb.push(label + ": còn nội dung cần kiểm tra lại với thẻ.");
      return r.text;
    }
    var ten = g.VNText.hoaDauTu(text(p[2], "Họ tên")), dc = text(p[5], "Địa chỉ");
    if (!ten) loi.push("QR thiếu họ tên khách.");
    var sinh = ngay(p[3]), cap = ngay(p[6]);
    if (p[3] && !sinh) loi.push("Ngày sinh không hợp lệ (cần ddmmyyyy và ngày có thật).");
    if (p[6] && !cap) loi.push("Ngày cấp không hợp lệ (cần ddmmyyyy và ngày có thật).");
    if (sinh && cap && cap < sinh) loi.push("Ngày cấp không được trước ngày sinh.");
    var gtText = text(p[4], "Giới tính").toLowerCase();
    var gt = gtText === "nam" ? "1" : (gtText === "nữ" || gtText === "nu" ? "0" : "");
    if (!sinh) thieu.push("Ngày sinh");
    if (!cap) thieu.push("Ngày cấp");
    if (!dc) thieu.push("Địa chỉ");
    if (!gt) thieu.push("Giới tính");
    if (thieu.length) cb.push("Chưa xác định: " + thieu.join(", ") + ". Kiểm tra và bổ sung từ thẻ.");
    if (!loi.length) result.data = { cmnd: p[0], cmnd_cu: p[1], ho_ten: ten,
      ngay_sinh: sinh, gioi_tinh: gt, dia_chi: dc, ngay_cap: cap, canh_bao: cb };
    return result;
  }
  g.CCCD = { parse: parse, ngay: ngay };
})(window);
