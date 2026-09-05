r"""
DỌN NGÀY SINH KHÁCH trên PMV (GĐ duyệt 05/09/2026) — ngoại lệ RULE 2 duy nhất, chạy TAY.

Vấn đề: app PMVGoldRT ghi BirthDate = ngày tạo hồ sơ khi người bán để trống → 56.392/56.401 khách
có ngày sinh > 2010 (đo 05/09/2026). Tiêu chí GĐ: ngày sinh > 2010-01-01 = không hợp lệ → về
01/01/1900 (giá trị default của cột). CHỈ đổi cột BirthDate, không đụng gì khác, không job đêm.

  manage.py sua_ngay_sinh_khach --kiem                 chỉ đếm (KK + sandbox), không ghi
  manage.py sua_ngay_sinh_khach --dich sandbox         tập dượt trên bản thử
  manage.py sua_ngay_sinh_khach --dich kk --xac-nhan KK   chạy thật, theo lô --lo (mặc định 5000, ~1s/lô)
"""
import re
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv import behavior_log as BL, gateway
from apps.pmv.gateway import SUA_NGAY_SINH_SQL, pmv_admin, pmv_read
from apps.pmv.sandbox import sandbox_exec_khuon, sandbox_query

KHUON = re.compile(r"(?is)^\s*UPDATE\s+(?:TOP\s*\(\s*\d{1,5}\s*\)\s+)?I_CUSTOMER\s+SET\s+BirthDate\s*=\s*'1900-01-01'\s+"
                   r"WHERE\s+BirthDate\s*>\s*CAST\('2010-01-01'\s+AS\s+datetime\)\s*;?\s*$")
DEM_SQL = ("SELECT COUNT(*) AS tong, SUM(CASE WHEN BirthDate > CAST('2010-01-01' AS datetime) THEN 1 ELSE 0 END) AS sai, "
           "SUM(CASE WHEN BirthDate = '1900-01-01' THEN 1 ELSE 0 END) AS da_1900 FROM I_CUSTOMER WITH (NOLOCK)")


class Command(BaseCommand):
    help = "Dọn ngày sinh khách > 2010-01-01 → 1900-01-01 (chỉ 1 cột, GĐ duyệt 05/09/2026)"

    def add_arguments(self, parser):
        parser.add_argument("--dich", choices=["sandbox", "kk"], default="sandbox")
        parser.add_argument("--lo", type=int, default=5000)
        parser.add_argument("--kiem", action="store_true", help="Chỉ đếm, không ghi")
        parser.add_argument("--xac-nhan", default="", help="Gõ KK để ghi vào máy KK thật")

    def _dem(self, dich):
        r = pmv_read(DEM_SQL, tag="sua_ngay_sinh", audit=False) if dich == "kk" else sandbox_query(DEM_SQL)
        return r[0]

    def handle(self, *args, **o):
        # tự kiểm khuôn — mọi biến thể khác phải bị từ chối
        assert KHUON.match(SUA_NGAY_SINH_SQL.format(lo=5000))
        for xau in ("UPDATE I_CUSTOMER SET BirthDate='1900-01-01'", "UPDATE I_CUSTOMER SET Gender=1 WHERE BirthDate > CAST('2010-01-01' AS datetime)",
                    "UPDATE I_CUSTOMER SET BirthDate = '1900-01-01' WHERE 1=1", "DELETE FROM I_CUSTOMER"):
            assert not KHUON.match(xau), xau
        if o["lo"] < 1 or o["lo"] > 20000:
            raise CommandError("--lo trong khoảng 1..20000")

        for d in ("kk", "sandbox"):
            try:
                c = self._dem(d)
                self.stdout.write(f"[{d:7}] tổng {c['tong']} · sai (>2010) {c['sai']} · đã 1900 {c['da_1900']}")
            except Exception as exc:
                self.stdout.write(f"[{d:7}] không đếm được: {exc}")
        if o["kiem"]:
            return

        dich = o["dich"]
        if dich == "kk" and o["xac_nhan"] != "KK":
            raise CommandError("Ghi vào máy KK THẬT phải kèm --xac-nhan KK")
        truoc = self._dem(dich)
        sql = SUA_NGAY_SINH_SQL.format(lo=o["lo"])
        t0 = time.monotonic()
        tong, lo = 0, 0
        while True:
            if dich == "kk":
                n = pmv_admin(sql, tag="sua_ngay_sinh", database="PMV_BANLE_KH2", timeout=300, rowcount=True)
            else:
                n = sandbox_exec_khuon(sql, KHUON)
            lo += 1
            tong += max(n or 0, 0)
            self.stdout.write(f"  lô {lo}: {n} dòng")
            if not n or n < o["lo"]:
                break
            time.sleep(0.3)   # nhường máy trạm đang bán giữa các lô
        sau = self._dem(dich)
        gio = time.monotonic() - t0
        ok = sau["sai"] == 0 and sau["tong"] == truoc["tong"]
        tom = (f"SỬA NGÀY SINH KHÁCH [{dich}] {timezone.localtime():%d/%m/%Y %H:%M}: {tong} dòng → 1900-01-01 trong {lo} lô/{gio:.1f}s; "
               f"trước sai={truoc['sai']} sau sai={sau['sai']} · tổng khách {truoc['tong']}→{sau['tong']} · {'OK' if ok else 'LỆCH — KIỂM LẠI'}")
        if dich == "kk":
            gateway.canh_bao("sua_ngay_sinh", tom)
            BL.ghi_thay_doi(timezone.localdate(), [tom, "    I_CUSTOMER.BirthDate  (UPDATE thẳng 1 cột — ngoại lệ RULE 2, GĐ duyệt 05/09/2026)"])
        self.stdout.write((self.style.SUCCESS if ok else self.style.ERROR)(tom))
