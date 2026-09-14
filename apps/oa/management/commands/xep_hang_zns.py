# -*- coding: utf-8 -*-
"""QUÉT BÙ XẾP HÀNG ZNS — bù cho điểm yếu của móc đồng bộ trong ``bill.chot()``.

Móc chỉ bắt hóa đơn lập QUA MÀN HÌNH KHBL; hóa đơn lập từ app PMVGoldRT không đi qua đó.
Lệnh này quét ``TRN_RT_BUYSELL`` (Status='C', IsDel='0') trong N ngày và xếp những hóa đơn còn
thiếu — **cùng một hàm, cùng MỌI rào chắn** với móc.

⚠⚠ AN TOÀN — LỆNH NÀY NGUY HIỂM HƠN CÁI MÓC NHIỀU LẦN vì nó đi qua HÀNG NGHÌN hóa đơn, tức chạm
đúng 7.805 ``trn_id`` mà CARE360 đã có dòng trong bản chép. Vì vậy:
  · **XEM TRƯỚC là MẶC ĐỊNH** — muốn ghi thật phải gõ thêm ``--ghi``.
  · ``--ngay`` bị **chặn cứng ≤ 7**.
  · Chế độ xem trước chạy ĐÚNG mã thật rồi ``rollback`` — con số in ra là con số thật, không phải
    ước lượng của một nhánh mô phỏng riêng.

⚠ LỆNH NÀY CHƯA ĐƯỢC GẮN VÀO ``config/scheduler.py`` — chỉ chạy tay. Gắn định kỳ khi Giám đốc
duyệt bước 2 (gọi API gửi).

Cách dùng:
    manage.py xep_hang_zns --ngay 1              # XEM TRƯỚC (không ghi gì)
    manage.py xep_hang_zns --ngay 1 --ghi        # ghi thật
    manage.py xep_hang_zns --ngay 3 --ghi --don  # kèm rà ngược: dòng chờ mà nguồn đã hỏng ⇒ hủy
    manage.py xep_hang_zns --xem                 # chỉ thống kê sổ, không đụng PMV
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.oa import xep_hang as XH
from apps.oa.models import ZaloMessage

GIOI_HAN_NGAY = 7          # chặn cứng — một lần gõ nhầm --ngay 90 là quét trọn bản chép
TRAN_RA_SOAT = 500         # số dòng tối đa của lượt rà ngược (mỗi dòng = 1 SELECT sang PMV)

SQL_QUET = (
    "SELECT b.TrnID,b.BillCode,b.TrnDate,b.TrnTime,b.CustID,b.PayAmount,b.EmpID,b.ShopID,"
    "b.TillID,b.Status,b.IsDel,b.CreatedDate,b.TrnDateTime_Upd,c.CustName,c.Phone,e.EmpName "
    "FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
    "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=b.CustID "
    "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=b.EmpID "
    "WHERE b.Status='C' AND b.IsDel='0' AND b.CreatedDate>=? AND b.CreatedDate<? "
    "ORDER BY b.CreatedDate, b.TrnID"
)

# Nhãn tiếng Việt cho từng mã kết quả (để GĐ đọc được bảng tổng kết).
NHAN = {
    "tao_moi": "TẠO MỚI dòng chờ gửi",
    "cap_nhat": "cập nhật dòng cũ của KHBL",
    "cap_nhat_sau_dua": "cập nhật sau khi đụng tiến trình khác",
    "hoi_sinh": "hồi sinh dòng đã hủy → chờ gửi",
    "terminal_giu_nguyen": "đã gửi rồi, giữ nguyên",
    "da_co_dong_he_khac": "ô khóa đã bị CARE360 chiếm — không đụng",
    "bi_mau_khac_phu": "hóa đơn đã có tin cùng mẫu (thường là của CARE360)",
    "phone_group_cooldown": "chặn bởi khoảng lặng cùng số + cùng nhóm tin",
    "truoc_moc_hieu_luc": "hóa đơn có TRƯỚC mốc hiệu lực của quy tắc",
    "queue_build_failed": "thiếu biến bắt buộc của mẫu",
    "mau_khong_gui_duoc": "mẫu chưa ENABLE/ACTIVE",
    "rule_khong_co_mau": "quy tắc không trỏ mẫu nào",
    "source_missing": "không đọc được hóa đơn",
    "source_not_completed": "hóa đơn chưa chốt",
    "source_deleted": "hóa đơn đã xóa",
    "customer_missing": "hóa đơn không có mã khách",
    "phone_invalid": "khách không có số điện thoại hợp lệ",
    "loi_ngoai_le": "LỖI ngoại lệ (xem logs/zns_xep_hang.log)",
    "bo_qua_sau_dua": "bỏ qua vì tiến trình khác vừa chèn",
    "trung_tracking_3_lan": "trùng tracking_id 3 lần liên tiếp",
}


class Command(BaseCommand):
    help = "Quét bù, xếp hàng ZNS cho hóa đơn đã chốt trong N ngày (mặc định CHỈ XEM TRƯỚC)"

    def add_arguments(self, p):
        p.add_argument("--ngay", type=int, default=1, help=f"số ngày lùi lại (tối đa {GIOI_HAN_NGAY})")
        p.add_argument("--ghi", action="store_true", help="GHI THẬT (mặc định chỉ xem trước)")
        p.add_argument("--don", action="store_true",
                       help="kèm rà ngược: dòng KHBL đang chờ mà nguồn hết đủ điều kiện ⇒ hủy")
        p.add_argument("--xem", action="store_true", help="chỉ thống kê sổ, KHÔNG đụng PMV")

    # ───────────────────────────── vào ─────────────────────────────
    def handle(self, *a, **o):
        if o["xem"]:
            return self._thong_ke()
        ngay = int(o["ngay"])
        if ngay < 1 or ngay > GIOI_HAN_NGAY:
            raise CommandError(f"--ngay phải trong khoảng 1..{GIOI_HAN_NGAY} (chặn cứng để không "
                               f"quét trọn bản chép lịch sử của CARE360).")
        ghi = bool(o["ghi"])

        duoc, ly_do = XH._duoc_ghi_moi()
        if not duoc:
            self.stdout.write(self.style.WARNING(
                f"CÔNG TẮC ĐANG CHẶN ĐƯỜNG GHI: {ly_do}. "
                "Vẫn chạy XEM TRƯỚC để GĐ thấy con số; muốn ghi thì mở công tắc "
                "(ZNS_XEP_HANG trong .env + RESET, xóa STOP_ZNS.flag, PMV_TARGET=kk)."))
            ghi = False

        den = timezone.localtime()
        tu = den - timedelta(days=ngay)
        self.stdout.write(f"Quét hóa đơn CHỐT từ {tu:%d/%m/%Y %H:%M} đến {den:%d/%m/%Y %H:%M} "
                          f"— chế độ: {'GHI THẬT' if ghi else 'XEM TRƯỚC (rollback)'}")

        from apps.pmv import gateway
        dich = gateway.dich_hien_tai()
        rows = gateway.pmv_read(
            SQL_QUET,
            # ⚠ truyền CHUỖI ISO: SQL Server đời cũ + ODBC 18 không bind được kiểu date/datetime.
            (tu.strftime("%Y-%m-%d %H:%M:%S"), den.strftime("%Y-%m-%d %H:%M:%S")),
            tag="zns_quet_bu", audit=False, target=dich, query_timeout=60,
        )
        self.stdout.write(f"  · đích PMV = {dich} · {len(rows)} hóa đơn trong khoảng")

        tong = {}
        with transaction.atomic():
            for bill in rows:
                for ma, n in XH.xep_hang_tu_hoa_don(bill, ghi_log=ghi).items():
                    tong[ma] = tong.get(ma, 0) + n
            if o["don"]:
                tong["da_huy_vi_nguon_hong"] = self._ra_soat_nguoc(dich)
            if not ghi:
                transaction.set_rollback(True)      # XEM TRƯỚC: chạy mã thật rồi trả sổ về nguyên trạng

        self._in_bang(tong)
        if tong.get("truoc_moc_hieu_luc"):
            self.stdout.write(self.style.WARNING(
                f"  ⓘ {tong['truoc_moc_hieu_luc']} lượt bị bỏ vì hóa đơn có TRƯỚC mốc hiệu lực "
                "của quy tắc (2 quy tắc thật đều effective_from = 11/08/2026) — không phải lệnh chạy hụt."))
        if not ghi:
            self.stdout.write(self.style.WARNING(
                "  ⓘ CHƯA GHI GÌ CẢ. Thêm --ghi để ghi thật."))

    # ──────────────────────────── phần việc ────────────────────────────
    def _ra_soat_nguoc(self, dich):
        """Chiều ngược của móc: hóa đơn bị mở lại / xóa Ở APP PMVGoldRT không đi qua KHBL nên
        không ai gọi ``huy_xep_hang`` — dòng chờ sẽ mồ côi. Rà và hủy đúng những dòng đó."""
        cho = list(ZaloMessage.objects
                   .filter(source_type=XH.NGUON_KHBL, status__in=("queued", "retry"))
                   .order_by("id")[:TRAN_RA_SOAT])
        dem = 0
        for m in cho:
            try:
                bill = XH.doc_hoa_don(m.trn_id)
            except Exception:
                continue                      # đọc hỏng thì KHÔNG dám kết luận là hết điều kiện
            du, ma = XH.nguon_du_dieu_kien(bill)
            if not du:
                dem += XH.huy_xep_hang_thuan(m.trn_id, ma, bill=bill)
        return dem

    def _thong_ke(self):
        """Chỉ đọc sổ. Đây là nơi duy nhất tra được 'vì sao hóa đơn X không có tin' ngoài file log —
        bảng sự kiện ``zalo_auto_send_events`` của CARE360 KHÔNG được chép sang ``khj_bl``."""
        self.stdout.write(self.style.MIGRATE_HEADING("SỔ zalo_messages — toàn bảng"))
        for r in ZaloMessage.objects.values("source_type", "status").annotate(n=Count("id")).order_by("source_type", "status"):
            self.stdout.write(f"  {r['source_type']:<16} {r['status']:<18} {r['n']:>7}")
        self.stdout.write(self.style.MIGRATE_HEADING("\nRiêng dòng KHBL — lý do hủy"))
        q = (ZaloMessage.objects.filter(source_type=XH.NGUON_KHBL)
             .values("cancel_reason").annotate(n=Count("id")).order_by("-n"))
        if not q:
            self.stdout.write("  (chưa có dòng khbl_invoice nào)")
        for r in q:
            self.stdout.write(f"  {str(r['cancel_reason'] or '—'):<28} {r['n']:>7}")
        self.stdout.write("\nChi tiết từng lượt bỏ qua nằm ở logs/zns_xep_hang.log")

    def _in_bang(self, tong):
        self.stdout.write(self.style.MIGRATE_HEADING("\nKẾT QUẢ"))
        if not tong:
            self.stdout.write("  (không có hóa đơn nào trong khoảng)")
            return
        for ma, n in sorted(tong.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  {n:>6}  {ma:<26} {NHAN.get(ma, '')}")
