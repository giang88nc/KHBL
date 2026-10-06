# ============================================================
#  cauhinh_khbl.ps1 - Cau hinh van hanh KHBL cho dong co vanhanh.ps1 (07/10/2026)
#  Cua vao duy nhat: D:\PYTHON\KHBL\RESET_KHBL.bat
#  CONG cua don vi KHBL (RESET_KHBL chi dung toi dung cac cong/tien trinh nay):
#     8101  web waitress (CHI loopback)     8100  Caddy HTTPS (LAN) -> 8101
#     8109  khoa chay don scheduler (run_scheduler tu giu)
#     8119  khoa vong giam sat
#  KHONG dung cau noi khach hang 18202 - no chay bang venv KHBL nhung THUOC KHCD
#  (RESET_KHCD quan ly).
#  Doi cong khoa scheduler: sua CA o day LAN KHBL_SCHEDULER_KHOA_CONG trong .env.
#  CHI ky tu ASCII.
# ============================================================
$G = 'D:\PYTHON\KHBL'
$PY = "$G\venv\Scripts\python.exe"
@{
    Ten          = 'KHBL'
    Goc          = $G
    ThuMucLog    = "$G\logs"
    TacVu        = 'KimHanh2-VanHanh-KHBL'
    CongGiamSat  = 8119
    ChuKyGiamSat = 60
    DichVu       = @('MySQL80')
    TepCan       = @($PY, "$G\manage.py", "$G\.env", "$G\ops\caddy\caddy.exe", "$G\ops\caddy\Caddyfile")
    # CA noi bo dung chung KIMHANH: chep sang runtime neu chua co (ke thua TURN_ON_KHBL cu)
    TruocKhiBat  = {
        $dich = 'D:\PYTHON\KHBL\runtime\caddy-data\pki\authorities\local'
        $nguon = 'D:\PYTHON\KIMHANH\runtime\caddy-data\pki\authorities\local'
        if (-not (Test-Path -LiteralPath "$dich\root.key") -and (Test-Path -LiteralPath "$nguon\root.key")) {
            New-Item -ItemType Directory -Path $dich -Force | Out-Null
            Copy-Item -LiteralPath "$nguon\root.crt", "$nguon\root.key" -Destination $dich -Force
        }
    }
    ThanhPhan    = @(
        @{ Ten = 'Web'; Cong = 8101; DauHieu = 'waitress'; ThuMuc = $G; ChoGiay = 45
            Lenh = "`"$PY`" -m waitress --listen=127.0.0.1:8101 --threads=8 --trusted-proxy=127.0.0.1 --trusted-proxy-headers=x-forwarded-proto config.wsgi:application"
            Log = "$G\logs\server.log"; Env = @{ PYTHONUTF8 = '1' } }
        @{ Ten = 'Caddy'; Cong = 8100; DauHieu = 'Caddyfile'; ThuMuc = $G; ChoGiay = 20
            Lenh = "`"$G\ops\caddy\caddy.exe`" run --config `"$G\ops\caddy\Caddyfile`" --adapter caddyfile"
            Log = "$G\logs\caddy.log"
            Http = @{ Url = 'https://localhost:8100/'; Ma = '^(200|302)$' } }
        @{ Ten = 'Scheduler'; Cong = 8109; DauHieu = 'run_scheduler'; ThuMuc = $G; ChoGiay = 60
            Lenh = "`"$PY`" manage.py run_scheduler"
            Log = "$G\logs\scheduler.log"; Env = @{ PYTHONUTF8 = '1' } }
    )
    DonSot       = @(
        @{ Ten = 'WATCHDOG_KHBL cu'; DauHieu = 'WATCHDOG_KHBL' }
        @{ Ten = 'TURN_ON_KHBL cu'; DauHieu = 'TURN_ON_KHBL' }
        @{ Ten = 'scheduler khong giu khoa'; DauHieu = 'run_scheduler'; CanGoc = $true }
    )
}
