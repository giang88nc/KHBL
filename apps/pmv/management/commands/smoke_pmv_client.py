"""
Smoke Track A3: PmvClient + kênh gọi proc. CHỈ ĐỌC trên PMV thật; ghi (nếu có) chỉ trên sandbox.
Chạy: manage.py smoke_pmv_client
"""
from django.core.management.base import BaseCommand

from apps.pmv.client import PmvClient
from apps.pmv.gateway import PmvBlocked, pmv_call
from apps.pmv.models import PmvAudit, PmvState, PmvUser


class Command(BaseCommand):
    help = "Smoke test PmvClient / pmv_call (đọc thật, thử trên sandbox)"

    def handle(self, *args, **opts):
        ok = fail = 0

        def check(name, cond):
            nonlocal ok, fail
            ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
            self.stdout.write(f"  {'PASS' if cond else 'FAIL'}  {name}")

        # 1. định dạng
        import datetime
        check("fmt_date dd/MM/yyyy", PmvClient.fmt_date(datetime.date(2026, 9, 3)) == "03/09/2026")
        check("fmt_time HH:mm:ss", PmvClient.fmt_time(datetime.datetime(2026, 9, 3, 8, 5, 9)) == "08:05:09")
        check("money chuỗi nguyên", PmvClient.money(7050000.0) == "7050000")
        xml = PmvClient.xml_dataset("TRN_RT_BUYSELL_SELL", [{"STT": 1, "ProductDesc": "Nhẫn 1 chỉ <9999>", "X": None}])
        check("xml_dataset escape + bỏ None", xml == "<NewDataSet><TRN_RT_BUYSELL_SELL><STT>1</STT><ProductDesc>Nhẫn 1 chỉ &lt;9999&gt;</ProductDesc></TRN_RT_BUYSELL_SELL></NewDataSet>")

        # 2. chữ ký proc từ sys.parameters (PMV thật, chỉ đọc)
        c = PmvClient("pmv", tag="smoke")
        prm = c.params_of("TRN_RT_BUYSELL_Ins")
        check("params_of TRN_RT_BUYSELL_Ins có p_Trn_RT_BUYSELL xml", any(n == "p_Trn_RT_BUYSELL" and t == "xml" for n, t in prm))
        try:
            c.call("TRN_RT_BUYSELL_Get", p_TrnID="x", p_SaiTen="")
            check("tham số sai tên bị chặn sớm", False)
        except ValueError:
            check("tham số sai tên bị chặn sớm", True)

        # 3. gọi proc ĐỌC trên PMV thật qua gateway
        tid = c.query("SELECT TOP 1 TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE Status='C' AND IsDel='0' ORDER BY CreatedDate DESC")[0]["TrnID"]
        b = c.bill(tid)
        check(f"bill() PMV thật {tid}: header + dòng hàng", b["header"] is not None and b["header"]["TrnID"] == tid and len(b["lines"]) >= 1)
        check("header có TrnDateTime_Upd (khóa lạc quan)", "TrnDateTime_Upd" in b["header"] or "TRN_GDN" in b["header"])
        check("sys_param TaoSoHDKhiThanhToan = '0'", c.sys_param("TaoSoHDKhiThanhToan") == "0")

        # 4. allowlist: proc GHI chưa duyệt → BLOCKED; proc lạ → BLOCKED
        for proc, write in (("TRN_RT_BUYSELL_Ins", True), ("Del_AllData", True), ("rptSRT_PrintBill", False)):
            try:
                pmv_call(proc, {}, tag="smoke", write=write)
                check(f"pmv_call chặn {proc}", False)
            except PmvBlocked:
                check(f"pmv_call chặn {proc}", True)
        # write lock
        old = PmvState.get("pmv_write_lock")
        PmvState.set("pmv_write_lock", "1")
        try:
            from apps.pmv import gateway as gw
            gw.PROC_WRITE_ALLOW = frozenset({"I_XRATE_Ins"})
            try:
                pmv_call("I_XRATE_Ins", {}, tag="smoke", write=True)
                check("write_lock chặn proc ghi đã duyệt", False)
            except PmvBlocked:
                check("write_lock chặn proc ghi đã duyệt", True)
        finally:
            gw.PROC_WRITE_ALLOW = frozenset()
            PmvState.set("pmv_write_lock", old or "")

        # 5. sandbox: gọi cùng proc trên bản sao
        s = PmvClient("sandbox", tag="smoke")
        sb = s.bill(tid) if s.query("SELECT 1 AS x FROM TRN_RT_BUYSELL WHERE TrnID=?", (tid,)) else None
        check("sandbox bill() (nếu sandbox có phiếu này)", sb is None or sb["header"]["TrnID"] == tid)

        # 6. tài khoản web
        us = {u.user_name: u for u in PmvUser.objects.all()}
        check("sys_users có admin + kimhanh2", {"admin", "kimhanh2"} <= set(us))
        check("admin có TillID + EmpID", bool(us.get("admin") and us["admin"].till_id and us["admin"].emp_id))
        check("mật khẩu web là băm Django, không plain", all(u.password.startswith("pbkdf2_") for u in us.values()))
        check("audit ghi dòng EXEC cho bill()", PmvAudit.objects.filter(kind="EXEC", tag="smoke", ok=True).exists())

        self.stdout.write(self.style.SUCCESS(f"KẾT QUẢ: {ok} PASS / {fail} FAIL") if not fail else self.style.ERROR(f"KẾT QUẢ: {ok} PASS / {fail} FAIL"))
