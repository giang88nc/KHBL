# -*- coding: utf-8 -*-
"""smoke_oa — hồi quy XẾP HÀNG ZNS (``apps/oa/xep_hang.py``), 16 kịch bản.

    manage.py smoke_oa

⚠⚠ AN TOÀN — BẢNG ``zalo_messages`` CHỨA 16.657 DÒNG LỊCH SỬ GỬI THẬT.
  · **TUYỆT ĐỐI KHÔNG** ``python -m unittest`` / ``unittest discover`` / ``pytest`` — ngày 11/09/2026
    đã xóa sạch 355.918 dòng ``khj_bl`` vì chạy sai lệnh. CHỈ ``manage.py``.
  · Toàn bộ bài kiểm chạy TRONG MỘT ``transaction.atomic()`` rồi **rollback CƯỠNG BỨC** — kể cả các
    kịch bản có UPDATE cũng không để lại dấu vết (xóa theo danh sách id KHÔNG hoàn tác được UPDATE,
    nên rollback là lớp bảo vệ CHÍNH, danh sách id chỉ là lớp hai).
  · Lớp hai: ``finally`` rà lại dòng còn sót, và **chỉ xóa khi thỏa CẢ HAI** điều kiện
    ``source_type='khbl_invoice'`` VÀ ``trn_id`` bắt đầu bằng ``SMOKE-``. Sai một điều là DỪNG, báo
    đỏ, không xóa gì. KHÔNG BAO GIỜ ``TRUNCATE``, không ``DELETE`` theo điều kiện rộng.
  · ``COUNT(*)`` toàn bảng in ra ở đầu và cuối, phải BẰNG NHAU.
  · Hóa đơn đầu vào là dict GIẢ trong bộ nhớ — KHÔNG gọi PMV, KHÔNG gọi API Zalo.
  · Cột ``is_test`` KHÔNG TỒN TẠI trong DDL thật (bản smoke cũ dùng nó sẽ nổ ngay câu INSERT đầu);
    cách đánh dấu rác duy nhất là tiền tố ``SMOKE-`` trong ``trn_id`` (varchar(30)).
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.oa import xep_hang as XH
from apps.oa.models import ZaloMessage, ZaloSendRule, ZaloTemplate

TIEN_TO = "SMOKE-"
#: ngày 12/08/2026 — SAU mốc hiệu lực 11/08 của cả 2 quy tắc (nếu không thì mọi kịch bản đều bị
#: bỏ với mã ``truoc_moc_hieu_luc``).
NGAY_THU = datetime(2026, 8, 12)
UNG_VIEN_SDT = ["0912345678", "0977000111", "0355000111", "0399000111", "0866000111"]


class Command(BaseCommand):
    help = "Hồi quy bộ xếp hàng ZNS (apps/oa) — chạy trong transaction rồi rollback"

    def handle(self, *a, **o):
        self.dat, self.truot, self.ids = 0, [], []
        truoc = ZaloMessage.objects.count()
        self.stdout.write(f"Tổng dòng zalo_messages TRƯỚC: {truoc}")
        # Công tắc cấp tiến trình: chắc chắn không đường ghi "thật" nào lọt trong lúc kiểm.
        XH.tat_cho_tien_trinh(True)
        try:
            with transaction.atomic():
                try:
                    self._chay()
                finally:
                    transaction.set_rollback(True)     # ROLLBACK CƯỠNG BỨC
        finally:
            XH.tat_cho_tien_trinh(False)
            self._don_lop_hai()
        sau = ZaloMessage.objects.count()
        self.stdout.write(f"Tổng dòng zalo_messages SAU:   {sau}")
        self.ok("tổng số dòng toàn bảng KHÔNG đổi", truoc == sau, f"{truoc} → {sau}")
        self.stdout.write("")
        if self.truot:
            self.stdout.write(self.style.ERROR(f"TRƯỢT {len(self.truot)}/{self.dat + len(self.truot)}:"))
            for t in self.truot:
                self.stdout.write(self.style.ERROR(f"  x {t}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f"TẤT CẢ {self.dat} kịch bản PASS"))

    # ───────────────────────────── khung ─────────────────────────────
    def ok(self, ten, dieu_kien, chi_tiet=""):
        if dieu_kien:
            self.dat += 1
            self.stdout.write(self.style.SUCCESS(f"  v {ten}") + (f"  ({chi_tiet})" if chi_tiet else ""))
        else:
            self.truot.append(f"{ten}  ({chi_tiet})" if chi_tiet else ten)
            self.stdout.write(self.style.ERROR(f"  x {ten}") + (f"  ({chi_tiet})" if chi_tiet else ""))

    def bo_qua(self, ten, ly_do):
        """Mục KHÔNG kiểm được vì thiếu dữ liệu nền — báo vàng, KHÔNG tính là TRƯỢT.

        Sổ zalo_messages có thể TRỐNG một cách hợp lệ: Giám đốc đã xóa 16.657 dòng chép từ
        CARE360 ngày 14/09/2026 để sổ khj_bl bắt đầu sạch. Những mục đối chiếu với dòng
        CARE360 thật khi đó không có gì để so — đó là thiếu dữ liệu nền, không phải lỗi mã.
        """
        self.stdout.write(self.style.WARNING(f"  ~ {ten}  (BỎ QUA: {ly_do})"))

    def muc(self, ten):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{ten}"))

    def _don_lop_hai(self):
        """Lớp bảo vệ THỨ HAI. Sau rollback thì không còn gì; nếu vẫn còn là có chuyện — kiểm ngặt
        rồi mới xóa, sai một điều kiện là dừng và báo đỏ."""
        try:
            con = list(ZaloMessage.objects.filter(trn_id__startswith=TIEN_TO))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ! không rà được dòng sót: {e}"))
            return
        if not con:
            return
        for m in con:
            if m.source_type != XH.NGUON_KHBL or not str(m.trn_id or "").startswith(TIEN_TO):
                self.stdout.write(self.style.ERROR(
                    f"  !! DỪNG: dòng #{m.id} không thỏa điều kiện rác "
                    f"(source_type={m.source_type!r}, trn_id={m.trn_id!r}) — KHÔNG XÓA GÌ."))
                return
        n = ZaloMessage.objects.filter(id__in=[m.id for m in con]).delete()[0]
        self.stdout.write(self.style.WARNING(f"  ! đã dọn {n} dòng sót (rollback không ăn?)"))

    # ───────────────────────────── dữ liệu ─────────────────────────────
    def _chon_sdt(self):
        """Chọn số thử CHƯA có trong 16.657 dòng thật — nếu trùng, khoảng lặng của dòng thật sẽ
        chen vào và kịch bản 8/12 báo trượt oan."""
        for so in UNG_VIEN_SDT:
            if not ZaloMessage.objects.filter(customer_phone=so).exists():
                return so
        return UNG_VIEN_SDT[0]

    def _bill(self, trn, *, sdt, gio=None, ten_kh="Chị A", tien="22010000",
              status="C", is_del="0", cust="CU2608000000001", bill_code=None):
        tao = gio or NGAY_THU.replace(hour=12, minute=3, second=43)
        return {
            "TrnID": trn, "BillCode": bill_code if bill_code is not None else f"{TIEN_TO}BC-01",
            "TrnDate": NGAY_THU, "TrnTime": "12:03:43", "CustID": cust,
            "PayAmount": Decimal(tien), "EmpID": "EMP150400000001",
            "ShopID": "TSP141100000001", "TillID": "TIL260500000001",
            "Status": status, "IsDel": is_del, "CreatedDate": tao,
            "TrnDateTime_Upd": None, "CustName": ten_kh, "Phone": sdt,
            "EmpName": "Nguyễn Văn A",
        }

    def _cua_toi(self, trn):
        return list(ZaloMessage.objects.filter(source_type=XH.NGUON_KHBL, trn_id=trn)
                    .order_by("send_rule_id"))

    def _thoi(self, trn, oa=1):
        """Raw SQL đọc giá trị NAIVE đúng như nằm trong bảng (kiểm múi giờ)."""
        with connection.cursor() as cur:
            cur.execute("SELECT send_rule_id, eligible_at, scheduled_at FROM zalo_messages "
                        "WHERE source_type=%s AND trn_id=%s AND oa_account_id=%s "
                        "ORDER BY send_rule_id", [XH.NGUON_KHBL, trn, oa])
            return cur.fetchall()

    # ───────────────────────────── các kịch bản ─────────────────────────────
    def _chay(self):
        sdt = self._chon_sdt()
        digits, local, masked, phash = XH.chuan_hoa_sdt(sdt)

        # ── 1-4: công thức khóa, đối chiếu với DÒNG THẬT (chỉ ĐỌC) ──
        self.muc("A. CÔNG THỨC KHÓA — đối chiếu dòng CARE360 thật")
        that = (ZaloMessage.objects.filter(source_type="auto_rule")
                .exclude(trn_id__isnull=True).order_by("-id").first())
        if that is None:
            self.bo_qua("1-4. đối chiếu công thức khóa với dòng CARE360 thật",
                        "sổ chưa có dòng auto_rule nào — công thức vẫn được khóa lại ở mục 5-9")
        else:
            vat = f"{that.oa_account_id}|{that.send_rule_id}|auto_rule|{that.trn_id}"
            self.ok("1. dedupe_key tính lại khớp dòng thật",
                    hashlib.sha256(vat.encode()).hexdigest() == that.dedupe_key,
                    f"#{that.id} · {vat}")
            d2 = "84" + str(that.customer_phone or "")[1:]
            self.ok("2. recipient_hash tính lại khớp dòng thật (tiền tố care360-zbs|)",
                    hashlib.sha256((XH.TIEN_TO_BAM + d2).encode()).hexdigest() == that.recipient_hash,
                    f"#{that.id}")
        self.ok("3. recipient_masked đúng dạng 3+***+3",
                XH.chuan_hoa_sdt("0769999200")[2] == "076***200",
                XH.chuan_hoa_sdt("0769999200")[2])
        self.ok("   khóa KHBL khác khóa CARE360 cho CÙNG hóa đơn",
                XH.khoa_chong_trung(1, 2, "X1") == hashlib.sha256(b"1|2|khbl_invoice|X1").hexdigest()
                and XH.khoa_chong_trung(1, 2, "X1") != hashlib.sha256(b"1|2|auto_rule|X1").hexdigest())

        # ── 5-9: dựng dòng từ một hóa đơn giả ──
        self.muc("B. DỰNG DÒNG TỪ MỘT HÓA ĐƠN")
        trn1 = f"{TIEN_TO}0001"
        ket = XH.xep_hang_tu_hoa_don(self._bill(trn1, sdt=sdt), ghi_log=False)
        rows = self._cua_toi(trn1)
        self.ids += [r.id for r in rows]
        self.ok("8. một hóa đơn ⇒ ĐÚNG 2 dòng (2 quy tắc đang bật)", len(rows) == 2, str(ket))
        if len(rows) == 2:
            r1, r2 = rows                                   # rule 1 (CUSTOMER_CARE), rule 2 (TRANSACTION)
            self.ok("   nhãn / trạng thái / kênh / giá / lượt thử",
                    all(r.source_type == "khbl_invoice" and r.status == XH.TRANG_THAI_GHI_SO
                        and r.channel == "phone" and r.estimated_cost == Decimal("300.00")
                        and r.attempt_count == 0 and r.source_eligible is True
                        and r.recipient_ciphertext == "khbl_no_cipher"
                        and r.customer_phone == local and r.recipient_hash == phash
                        and r.recipient_masked == masked
                        and r.source_status == "C" and r.pay_amount == Decimal("22010000.000")
                        and r.source_ref == trn1 and r.cust_id == "CU2608000000001"
                        for r in rows),
                    f"{r1.external_template_id} + {r2.external_template_id}")
            self.ok("   dedupe_key từng dòng đúng công thức",
                    all(r.dedupe_key == XH.khoa_chong_trung(r.oa_account_id, r.send_rule_id, trn1)
                        for r in rows))
            self.ok("4. tracking_id dài 32, toàn hex, duy nhất trong bảng",
                    all(len(r.tracking_id) == 32 and re.fullmatch(r"[0-9a-f]{32}", r.tracking_id)
                        and ZaloMessage.objects.filter(tracking_id=r.tracking_id).count() == 1
                        for r in rows))
            # 5 — MÚI GIỜ: hóa đơn 12:03:43 giờ VN ⇒ cột trong bảng phải là 05:03:43 (UTC naive)
            tho = self._thoi(trn1)
            self.ok("5. múi giờ: CreatedDate 12:03:43 VN ⇒ eligible_at 05:03:43 trong bảng",
                    all(e.hour == 5 and e.minute == 3 and e.second == 43 for _, e, _ in tho),
                    str([str(e) for _, e, _ in tho]))
            # 6 — lịch gửi đếm từ GIỜ TẠO HÓA ĐƠN
            delta = {rid: (s - e).total_seconds() / 60 for rid, e, s in tho}
            self.ok("6. scheduled_at − eligible_at = 3' (rule 1) và 5' (rule 2)",
                    delta.get(1) == 3 and delta.get(2) == 5, str(delta))
            # 7 — template_data_json
            mong = {
                "606575": '{"order_code":"SMOKE-BC-01","customer_name":"Chị A",'
                          '"price":"22010000","date":"12/08/2026"}',
                "606576": '{"customer_name":"Chị A","staff_name":"Nguyễn Văn A",'
                          '"order_code":"SMOKE-BC-01","date":"12/08/2026"}',
            }
            sai = [r.external_template_id for r in rows
                   if r.template_data_json != mong.get(r.external_template_id)]
            self.ok("7. template_data_json: đúng thứ tự khóa, tiền SỐ TRẦN, ngày dd/mm/yyyy, "
                    "CÓ dấu tiếng Việt, KHÔNG khoảng trắng", not sai,
                    rows[0].template_data_json)
            snap = json.loads(r2.rule_snapshot_json)
            self.ok("   rule_snapshot_json đủ 9 khóa đúng thứ tự",
                    list(snap) == ["rule_id", "rule_name", "message_group", "delay_minutes",
                                   "cooldown_minutes", "max_attempts", "retry_base_minutes",
                                   "template_id", "external_template_id"])
            tracking_cu = sorted(r.tracking_id for r in rows)
            # 9 — gọi lại lần 2 = UPSERT
            XH.xep_hang_tu_hoa_don(self._bill(trn1, sdt=sdt), ghi_log=False)
            lai = self._cua_toi(trn1)
            self.ok("9. gọi lần 2 cùng trn_id ⇒ VẪN 2 dòng, tracking_id KHÔNG đổi",
                    len(lai) == 2 and sorted(r.tracking_id for r in lai) == tracking_cu)

        # ── 10: hóa đơn CARE360 đã phục vụ ⇒ không đụng một cột nào ──
        self.muc("C. RÀO CHẮN VỚI 16.657 DÒNG LỊCH SỬ")
        trn_that = self._trn_care360_2_dong()
        if not trn_that:
            self.bo_qua("10. rào chắn với dòng CARE360 có sẵn",
                        "sổ chưa có hóa đơn CARE360 nào — rào vẫn còn trong mã, kiểm lại khi sổ có dòng")
        else:
            cu = self._chup(trn_that)
            n_truoc = ZaloMessage.objects.count()
            ket10 = XH.xep_hang_tu_hoa_don(self._bill(trn_that, sdt=sdt), ghi_log=False)
            moi = self._chup(trn_that)
            self.ok("10. hóa đơn CARE360 đã phục vụ ⇒ 0 dòng mới",
                    ZaloMessage.objects.count() == n_truoc
                    and not ZaloMessage.objects.filter(source_type=XH.NGUON_KHBL,
                                                       trn_id=trn_that).exists(),
                    f"{trn_that} · {ket10}")
            self.ok("    và 2 dòng CARE360 GIỮ NGUYÊN TỪNG CỘT (không chỉ đếm)", cu == moi,
                    "khớp toàn bộ cột" if cu == moi else "ĐÃ BỊ SỬA")

        # ── 11: khách không có số hợp lệ ──
        self.muc("D. KHÁCH KHÔNG SỐ · KHOẢNG LẶNG · HỦY")
        trn_x = f"{TIEN_TO}0002"
        ket11 = XH.xep_hang_tu_hoa_don(self._bill(trn_x, sdt="123"), ghi_log=False)
        self.ok("11. số điện thoại sai ⇒ 0 dòng, không ngoại lệ",
                not self._cua_toi(trn_x) and ket11 == {"phone_invalid": 1}, str(ket11))
        ket11b = XH.xep_hang_tu_hoa_don(self._bill(f"{TIEN_TO}0003", sdt=sdt, cust=""), ghi_log=False)
        self.ok("    hóa đơn không có mã khách ⇒ 0 dòng", ket11b == {"customer_missing": 1}, str(ket11b))

        # ── 12: khoảng lặng ──
        sdt2 = self._chon_sdt_khac(sdt)
        d2, local2, masked2, phash2 = XH.chuan_hoa_sdt(sdt2)
        moc = timezone.make_aware(NGAY_THU.replace(hour=12, minute=3, second=43), XH.GIO_VN)
        chan = self._dong_gia_cooldown(local2, phash2, moc - timedelta(minutes=30))
        self.ids.append(chan.id)
        trn2 = f"{TIEN_TO}0004"
        ket12 = XH.xep_hang_tu_hoa_don(self._bill(trn2, sdt=sdt2), ghi_log=False)
        r12 = self._cua_toi(trn2)
        self.ids += [r.id for r in r12]
        self.ok("12. khoảng lặng 60' chặn dòng CUSTOMER_CARE, dòng TRANSACTION vẫn được tạo",
                len(r12) == 1 and r12[0].send_rule_id == 2
                and ket12.get("phone_group_cooldown") == 1, str(ket12))

        # ── 13: hủy rồi hồi sinh ──
        n_huy = XH.huy_xep_hang_thuan(trn1, "source_not_completed")
        sau_huy = self._cua_toi(trn1)
        self.ok("13. huy_xep_hang ⇒ 2 dòng cancelled + source_eligible=0 + đúng cancel_reason",
                n_huy == 2 and all(r.status == "cancelled" and r.source_eligible is False
                                   and r.cancel_reason == "source_not_completed"
                                   and r.error_code == "source_not_eligible" for r in sau_huy),
                f"{n_huy} dòng")
        XH.xep_hang_tu_hoa_don(self._bill(trn1, sdt=sdt), ghi_log=False)
        hoi = self._cua_toi(trn1)
        self.ok("    xếp hàng lại ⇒ HỒI SINH về trạng thái ghi sổ, KHÔNG đẻ dòng mới",
                len(hoi) == 2 and all(r.status == XH.TRANG_THAI_GHI_SO and r.cancel_reason is None
                                      and r.error_code is None for r in hoi))
        self.ok("    dòng chết KHÔNG hồi sinh được nếu lỗi ngoài bộ cho phép",
                "task_activation_cutoff" not in XH.LOI_HOI_SINH_DUOC
                and "-118" not in XH.LOI_HOI_SINH_DUOC,
                "task_activation_cutoff / -118 / -141 bị loại")

        # ── 14: công tắc ──
        self.muc("E. CÔNG TẮC & NUỐT LỖI")
        XH.tat_cho_tien_trinh(False)
        goc_env, goc_dich = XH._co_bat_trong_env, XH._dich_hien_tai
        try:
            # (a) cờ .env TẮT ⇒ chặn (đây là giá trị MẶC ĐỊNH khi cài lên máy thật)
            XH._co_bat_trong_env = lambda: False
            XH._dich_hien_tai = lambda c=None: "kk"
            duoc, ly_do = XH._duoc_ghi_moi()
            self.ok("14. cờ ZNS_XEP_HANG tắt ⇒ đường ghi mới bị chặn",
                    not duoc and ly_do == "cong_tac_tat", ly_do)
            # (b) cờ BẬT nhưng có STOP_ZNS.flag ⇒ dừng khẩn, không cần RESET
            XH._co_bat_trong_env = lambda: True
            co = Path(str(settings.BASE_DIR)) / XH.TEN_CO_DUNG_KHAN
            da_co = co.exists()
            if not da_co:
                co.write_text("smoke_oa", encoding="utf-8")
            try:
                duoc2, ly_do2 = XH._duoc_ghi_moi()
            finally:
                if not da_co:
                    co.unlink(missing_ok=True)
            self.ok("    STOP_ZNS.flag ⇒ dừng khẩn ngay, không cần RESET",
                    not duoc2 and ly_do2 == "co_dung_khan", ly_do2)
            self.ok("    tệp cờ đã được dọn sạch", da_co or not co.exists())
            # (c) cờ BẬT, không cờ dừng, nhưng đích là sandbox ⇒ vẫn chặn
            XH._dich_hien_tai = lambda c=None: "sandbox"
            duoc3, ly_do3 = XH._duoc_ghi_moi()
            self.ok("    đích sandbox ⇒ vẫn chặn (không để dữ liệu bản thử vào SỔ THẬT)",
                    not duoc3 and ly_do3.startswith("dich_khong_phai_kk"), ly_do3)
        finally:
            XH._co_bat_trong_env, XH._dich_hien_tai = goc_env, goc_dich
            XH.tat_cho_tien_trinh(True)
        n_truoc = ZaloMessage.objects.count()
        ket14 = XH.xep_hang_hoa_don(f"{TIEN_TO}9999")
        self.ok("    xep_hang_hoa_don khi bị chặn ⇒ 0 dòng, trả rỗng",
                ket14 == {} and ZaloMessage.objects.count() == n_truoc, str(ket14))

        # ── 15: lỗi MySQL không được làm hỏng đường chốt ──
        goc_doc, goc_co = XH.doc_hoa_don, XH._duoc_ghi_moi

        def _no(*a, **k):
            raise RuntimeError("giả lập MySQL/PMV sập")
        try:
            XH.doc_hoa_don = _no
            XH._duoc_ghi_moi = lambda c=None: (True, "ok")
            ket15 = XH.xep_hang_hoa_don(f"{TIEN_TO}8888")
            self.ok("15. lỗi khi xếp hàng ⇒ xep_hang_hoa_don TRẢ VỀ BÌNH THƯỜNG, KHÔNG NÉM "
                    "(hóa đơn vẫn chốt)", ket15 == {}, str(ket15))
        except Exception as e:
            self.ok("15. lỗi khi xếp hàng không được ném ra ngoài", False, repr(e))
        finally:
            XH.doc_hoa_don, XH._duoc_ghi_moi = goc_doc, goc_co

        goc_save = ZaloMessage.save
        try:
            ZaloMessage.save = _no
            ket15b = XH.xep_hang_tu_hoa_don(self._bill(f"{TIEN_TO}0005", sdt=sdt), ghi_log=False)
            self.ok("    lỗi ghi MySQL ⇒ nuốt trọn, chỉ đếm vào loi_ngoai_le",
                    ket15b.get("loi_ngoai_le", 0) >= 1, str(ket15b))
        except Exception as e:
            self.ok("    lỗi ghi MySQL không được ném ra ngoài", False, repr(e))
        finally:
            ZaloMessage.save = goc_save

        # ── 16: rào tĩnh chống gọi API ──
        self.muc("F. RÀO TĨNH — BƯỚC 1 TUYỆT ĐỐI KHÔNG GỬI")
        # Ghép từ mảnh để chính tệp này không tự dính bẫy grep.
        cam = ["requ" + "ests", "urlo" + "pen", "urll" + "ib", "http.cl" + "ient",
               "openapi.zalo" + ".me", "zalo_cl" + "ient", "Zalo" + "Client", "encrypt_" + "value"]
        goc = Path(XH.__file__).resolve().parent
        dinh = []
        for p in sorted(goc.rglob("*.py")):
            noi_dung = p.read_text(encoding="utf-8", errors="replace")
            for tu in cam:
                if tu in noi_dung:
                    dinh.append(f"{p.name}:{tu}")
        self.ok("16. toàn bộ apps/oa/**.py KHÔNG chứa lời gọi mạng nào", not dinh, str(dinh))

    # ───────────────────────────── trợ giúp ─────────────────────────────
    def _chon_sdt_khac(self, da_dung):
        for so in UNG_VIEN_SDT:
            if so != da_dung and not ZaloMessage.objects.filter(customer_phone=so).exists():
                return so
        return UNG_VIEN_SDT[-1]

    def _trn_care360_2_dong(self):
        """trn_id mà CARE360 đã ghi ĐỦ CẢ 2 quy tắc — chọn động, không ghim mã cứng."""
        with connection.cursor() as cur:
            cur.execute("SELECT trn_id FROM zalo_messages WHERE source_type='auto_rule' "
                        "AND trn_id IS NOT NULL GROUP BY trn_id HAVING COUNT(*)=2 "
                        "ORDER BY MAX(id) DESC LIMIT 1")
            r = cur.fetchone()
        return r[0] if r else None

    def _chup(self, trn_id):
        """Chụp TOÀN BỘ cột của các dòng mang trn_id này — để so trước/sau từng cột."""
        with connection.cursor() as cur:
            cur.execute("SELECT * FROM zalo_messages WHERE trn_id=%s ORDER BY id", [trn_id])
            return [tuple(str(v) for v in row) for row in cur.fetchall()]

    def _dong_gia_cooldown(self, local, phash, moc):
        """Dòng CUSTOMER_CARE giả (mẫu 13, tag CUSTOMER_CARE) để kích hoạt khoảng lặng 60'."""
        mau = ZaloTemplate.objects.get(id=13)
        rule = ZaloSendRule.objects.get(id=1)
        bay_gio = timezone.now()
        m = ZaloMessage(
            oa_account_id=rule.oa_account_id, template_id=mau.id, send_rule_id=None,
            external_template_id=mau.template_id, channel="phone",
            source_type=XH.NGUON_KHBL, source_ref=f"{TIEN_TO}COOL",
            trn_id=f"{TIEN_TO}COOL", bill_code=f"{TIEN_TO}COOL", cust_id="CU2608000000009",
            customer_name="Khách thử", customer_phone=local,
            recipient_ciphertext=XH.KHONG_CO_MA_HOA, recipient_masked=f"{local[:3]}***{local[-3:]}",
            recipient_hash=phash, template_data_json="{}",
            tracking_id=uuid.uuid4().hex[:32],
            dedupe_key=hashlib.sha256(f"smoke-cooldown|{local}".encode()).hexdigest(),
            status="queued", eligible_at=moc, scheduled_at=moc, attempt_count=0,
            source_eligible=True, created_at=bay_gio, updated_at=bay_gio,
        )
        m.save(force_insert=True)
        return m
