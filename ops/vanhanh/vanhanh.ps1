# ============================================================
#  vanhanh.ps1 - DONG CO VAN HANH CHUNG cac du an Kim Hanh 2 (07/10/2026, GD chot)
#
#  BAN SAO GIONG HET o 3 noi - sua 1 cho thi CHEP sang ca 3:
#     D:\PYTHON\KHJ\ops\vanhanh\vanhanh.ps1    (dung cho KHJ va cum NGROK)
#     D:\PYTHON\KHBL\ops\vanhanh\vanhanh.ps1
#     D:\PYTHON\KHCD\ops\vanhanh\vanhanh.ps1
#  (scripts\kiem_sau_khoi_dong.ps1 cua KHJ so sha256 3 ban, lech la bao LOI)
#  Moi don vi co 1 tep cau hinh rieng cauhinh_<ten>.ps1 canh tep nay: thanh phan,
#  cong, lenh bat, dau hieu nhan dien.
#
#  NGUOI DUNG KHONG GOI TRUC TIEP - bam RESET_<TEN>.bat o goc du an:
#    reset   kiem dieu kien -> TAT het -> kiem da tat sach -> BAT lai -> kiem OK
#    bat     chi bat cai dang thieu (luc khoi dong may / KHOI_DONG_TOAN_HE_THONG)
#    tat     tat han de bao tri (giam sat cung tat - khong ai tu bat lai cho toi
#            lan bat/reset/khoi dong may sau)
#    kiem    chi xem, khong doi gi - chay duoc o quyen thuong
#    giamsat vong canh gac: moi ChuKyGiamSat giay bat lai cai chet. Do 'bat' tu khoi
#            chay; giu CONG KHOA rieng nen khong the co 2 vong canh gac
#    yeucau  chi tac vu Windows "KimHanh2-VanHanh-<TEN>" goi: xu ly cac yeu cau ma
#            RESET_<TEN>.bat (quyen thuong) de lai trong <ThuMucLog>\vanhanh\<ten>\
#
#  VI SAO qua tac vu Windows: reset/bat/tat can quyen Admin (giet duoc tien trinh muc
#  Admin cua luot khoi dong may). Tac vu "KimHanh2-VanHanh-<TEN>" (S4U, quyen cao
#  nhat, khong lich) cho quyen thuong kich chay KHONG hoi UAC; tien trinh bat ra nam
#  o phien nen (session 0) - song qua dang xuat, khong dinh job cua app goi (bai hoc
#  15/09: goi tu app Claude roi dong app la ca tiem sap). Chua co tac vu thi xin UAC.
#
#  NGUYEN TAC (deu la bai hoc that):
#   1) NHAN DIEN BANG CONG: ai dang nghe cong nao doc duoc o MOI muc quyen. Scheduler
#      va vong giam sat moi cai giu 1 CONG KHOA rieng => khong the chay 2 ban. Quyen
#      thuong KHONG doc duoc dong lenh tien trinh muc Admin - dung ten cong, khong dung
#      dong lenh de quyet dinh "da chay chua".
#   2) Chi GIET khi CHAC la cua don vi nay: dong lenh (tien trinh hoac cha/ong) khop
#      DauHieu VA thuoc thu muc du an. Ung dung khac giu cong -> bao LOI, KHONG giet.
#   3) Khong doc duoc thi noi khong doc duoc - khong doan.
#   4) Thieu dieu kien (dich vu MySQL, venv, khoa...) thi RESET KHONG tat gi ca.
#   5) Tien trinh con bat bang Start-Process KHONG chuyen huong (ShellExecute): khong
#      ke thua handle (cong khoa, ong dan) cua tien trinh goi.
#  File nay CHI chua ky tu ASCII (PowerShell 5.1 doc sai chu Viet trong .ps1 khong BOM).
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$CauHinh,
    [ValidateSet('reset', 'bat', 'tat', 'kiem', 'giamsat', 'yeucau')][string]$Lenh = 'kiem'
)
$ErrorActionPreference = 'Continue'
$CauHinh = (Resolve-Path -LiteralPath $CauHinh).Path
$CH = & $CauHinh
$TEN = $CH.Ten
$GOC = $CH.Goc
$YCDIR = Join-Path $CH.ThuMucLog ('vanhanh\' + $TEN.ToLower())
New-Item -ItemType Directory -Path $YCDIR -Force | Out-Null
$LOGFILE = Join-Path $CH.ThuMucLog ('vanhanh_' + $TEN.ToLower() + '.log')
$CURL = Join-Path $env:SystemRoot 'System32\curl.exe'
$CMD = Join-Path $env:SystemRoot 'System32\cmd.exe'
$PSEXE = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$PHIEN = (Get-Process -Id $PID).SessionId
# Vong giam sat nhan dien bang TEN TEP CAU HINH (KHJ va NGROK cung dong co trong thu muc KHJ)
$GIAMSAT = @{ Ten = 'Giam sat'; Cong = [int]$CH.CongGiamSat; KhongCanGoc = $true
    DauHieu = ([regex]::Escape((Split-Path -Leaf $CauHinh)) + '.*-Lenh giamsat') }
$TEN_GOC_CAY = @('cmd.exe', 'python.exe', 'php.exe', 'caddy.exe', 'ngrok.exe')

$script:Loi = 0
$script:Im = $false          # che do giam sat: chi ghi khi CO VIEC (bat lai / loi)
$script:TepBaoCao = $null    # dang xu ly yeu cau: chep tung dong sang tep .part cho nguoi goi doc
$script:TT = @{}
$script:ToTien = @{}

# ---------------------------------------------------------------- ghi chep
function Ghi([string]$s, [switch]$Thuong) {
    if ($script:Im -and $Thuong) { return }
    if (-not $script:Im) { Write-Host $s }
    if ($script:TepBaoCao) { try { Add-Content -LiteralPath $script:TepBaoCao -Value $s } catch { } }
    try {
        if ((Test-Path -LiteralPath $LOGFILE) -and (Get-Item -LiteralPath $LOGFILE).Length -gt 5MB) {
            Move-Item -LiteralPath $LOGFILE -Destination ($LOGFILE + '.cu') -Force
        }
        Add-Content -LiteralPath $LOGFILE -Value ('{0:yyyy-MM-dd HH:mm:ss} [{1}/{2}] {3}' -f (Get-Date), $Lenh, $PID, $s)
    } catch { }
}
function Hong([string]$s) { $script:Loi++; Ghi ('  [LOI] ' + $s) }
function LaAdmin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- tien trinh
function ChupTienTrinh {
    $script:TT = @{}
    Get-CimInstance Win32_Process | ForEach-Object { $script:TT[[int]$_.ProcessId] = $_ }
    # to tien cua CHINH tien trinh nay (khong bao gio duoc giet)
    $script:ToTien = @{}
    $p = $script:TT[[int]$PID]
    for ($i = 0; $p -and $i -lt 10; $i++) {
        $script:ToTien[[int]$p.ProcessId] = $true
        $p = $script:TT[[int]$p.ParentProcessId]
    }
}
function ChuoiTT($p) { if (-not $p) { return '' }; return ([string]$p.CommandLine + ' | ' + [string]$p.ExecutablePath) }
function DocDuoc($p) { return [bool]($p -and ($p.CommandLine -or $p.ExecutablePath)) }
function DayTo([int]$ma) {
    $ds = New-Object System.Collections.ArrayList
    $p = $script:TT[$ma]
    for ($i = 0; $p -and $i -lt 4; $i++) {
        [void]$ds.Add($p)
        $cha = $script:TT[[int]$p.ParentProcessId]
        if (-not $cha -or $cha.CreationDate -gt $p.CreationDate) { break }   # PID da bi tai su dung
        $p = $cha
    }
    return , $ds
}
function GocCuaTp($tp) { if ($tp.Goc) { return $tp.Goc } else { return $GOC } }
# 'cua' | 'khac' | 'khongdoc' | 'mat'
function CuaDonVi($tp, [int]$ma) {
    $day = DayTo $ma
    if ($day.Count -eq 0) { return 'mat' }
    if (-not (DocDuoc $day[0])) { return 'khongdoc' }
    $g = GocCuaTp $tp
    $dau = $false
    $thuoc = [bool]$tp.KhongCanGoc
    foreach ($p in $day) {
        $s = ChuoiTT $p
        if ($s -match $tp.DauHieu) { $dau = $true }
        if ($s -like ('*' + $g + '*')) { $thuoc = $true }
    }
    if ($dau -and $thuoc) { return 'cua' } else { return 'khac' }
}
# $null = khong do duoc cong ; @() = khong ai nghe
function NguoiNghe([int]$cong) {
    try { $c = @(Get-NetTCPConnection -LocalPort $cong -State Listen -ErrorAction Stop) }
    catch {
        if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') { return , @() }
        return $null
    }
    return , @($c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { [int]$_ })
}
# Tien trinh cung dau hieu cua thanh phan (dang khoi dong / treo / ban cu khong giu cong)
function TimTienTrinh($tp) {
    $g = GocCuaTp $tp
    $kq = @()
    foreach ($p in $script:TT.Values) {
        if ($script:ToTien[[int]$p.ProcessId]) { continue }
        if ($TEN_GOC_CAY -notcontains $p.Name.ToLower()) { continue }
        $s = ChuoiTT $p
        if ($s -match 'vanhanh\.ps1') { continue }
        if ($s -notmatch $tp.DauHieu) { continue }
        if (-not $tp.KhongCanGoc) {
            $thuoc = $false
            foreach ($q in (DayTo ([int]$p.ProcessId))) { if ((ChuoiTT $q) -like ('*' + $g + '*')) { $thuoc = $true } }
            if (-not $thuoc) { continue }
        }
        $kq += $p
    }
    return , $kq
}
# Leo len cha/ong con cung dau hieu de giet ca cay (vo cmd + trinh khoi chay venv + tien trinh that)
function GocCay([int]$ma, $tp) {
    $day = DayTo $ma
    $goc = $day[0]
    for ($i = 1; $i -lt $day.Count; $i++) {
        $p = $day[$i]
        if ($script:ToTien[[int]$p.ProcessId]) { break }
        if ($TEN_GOC_CAY -notcontains $p.Name.ToLower()) { break }
        if ((ChuoiTT $p) -notmatch $tp.DauHieu) { break }
        $goc = $p
    }
    return $goc
}
function GietCay($p, [string]$lyDo) {
    if ($script:ToTien[[int]$p.ProcessId]) { return }
    Ghi ('  {0}: PID {1} {2}' -f $lyDo, $p.ProcessId, $p.Name)
    & taskkill.exe /T /F /PID $p.ProcessId 2>&1 | Out-Null
}
function TuoiGiay($p) { try { return ((Get-Date) - $p.CreationDate).TotalSeconds } catch { return 0 } }

# ---------------------------------------------------------------- kiem tra
function KiemDieuKien {
    $ok = $true
    foreach ($dv in @($CH.DichVu)) {
        if (-not $dv) { continue }
        $s = Get-Service -Name $dv -ErrorAction SilentlyContinue
        if (-not $s) { Hong "thieu dich vu Windows $dv"; $ok = $false; continue }
        $het = (Get-Date).AddSeconds(120)
        $daBao = $false
        while ($s.Status -ne 'Running' -and (Get-Date) -lt $het) {
            if (-not $daBao) { Ghi "  cho dich vu $dv (toi da 120s)..." -Thuong; $daBao = $true }
            Start-Sleep -Seconds 5
            $s.Refresh()
        }
        if ($s.Status -ne 'Running') { Hong "dich vu $dv khong chay"; $ok = $false }
    }
    foreach ($f in @($CH.TepCan)) {
        if ($f -and -not (Test-Path -LiteralPath $f)) { Hong "thieu tep $f"; $ok = $false }
    }
    return $ok
}
function KiemHttp($tp) {
    if (-not $tp.Http) { return $true }
    $ma = ''
    for ($i = 0; $i -lt 12; $i++) {
        $ma = (& $CURL -sk -o NUL -w '%{http_code}' --max-time 8 $tp.Http.Url 2>$null)
        if ($ma -match $tp.Http.Ma) {
            if (-not $tp.Http.NoiDung) { return $true }
            $than = (& $CURL -sk --max-time 8 $tp.Http.Url 2>$null) -join ' '
            $du = $true
            foreach ($r in @($tp.Http.NoiDung)) { if ($than -notmatch $r) { $du = $false } }
            if ($du) { return $true }
            $ma = "$ma (noi dung chua dat)"
        }
        Start-Sleep -Seconds 2
    }
    Hong ('{0}: {1} tra {2}, khong dat' -f $tp.Ten, $tp.Http.Url, $ma)
    return $false
}
function ChoCong($tp, [int]$giay) {
    $het = (Get-Date).AddSeconds($giay)
    while ((Get-Date) -lt $het) {
        $n = NguoiNghe $tp.Cong
        if ($n -and $n.Count -gt 0) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

# ---------------------------------------------------------------- tat
function TatThanhPhan($tp, [string]$nhan = 'tat') {
    $ds = NguoiNghe $tp.Cong
    if ($null -eq $ds) { Hong ('{0}: KHONG DO DUOC cong {1}' -f $tp.Ten, $tp.Cong); return }
    if ($ds.Count -eq 0) { Ghi ('  {0}: khong chay san' -f $tp.Ten) -Thuong }
    foreach ($ma in $ds) {
        $k = CuaDonVi $tp $ma
        if ($k -eq 'mat') { continue }
        if ($k -eq 'khongdoc') { Hong ('{0}: khong doc duoc tien trinh PID {1} giu cong {2} - can quyen Admin' -f $tp.Ten, $ma, $tp.Cong); continue }
        if ($k -eq 'khac') { Hong ('{0}: cong {1} do UNG DUNG KHAC giu (PID {2} {3}) - KHONG giet' -f $tp.Ten, $tp.Cong, $ma, $script:TT[$ma].Name); continue }
        GietCay (GocCay $ma $tp) ('{0}: {1}' -f $tp.Ten, $nhan)
    }
    # ban cung dau hieu nhung khong giu cong (dang khoi dong / treo / ban cu)
    ChupTienTrinh
    foreach ($p in (TimTienTrinh $tp)) { GietCay (GocCay ([int]$p.ProcessId) $tp) ('{0}: {1} (khong giu cong)' -f $tp.Ten, $nhan) }
}
function DonSot {
    ChupTienTrinh
    foreach ($m in @($CH.DonSot)) {
        if (-not $m) { continue }
        foreach ($p in @($script:TT.Values)) {
            if ($script:ToTien[[int]$p.ProcessId]) { continue }
            if (@('cmd.exe', 'python.exe', 'powershell.exe', 'wscript.exe', 'php.exe', 'caddy.exe') -notcontains $p.Name.ToLower()) { continue }
            $s = ChuoiTT $p
            if ($s -match 'vanhanh\.ps1') { continue }
            if ($s -notmatch $m.DauHieu) { continue }
            if ($m.CanGoc) {
                $thuoc = $false
                foreach ($q in (DayTo ([int]$p.ProcessId))) { if ((ChuoiTT $q) -like ('*' + $GOC + '*')) { $thuoc = $true } }
                if (-not $thuoc) { continue }
            }
            GietCay $p ('don sot ' + $m.Ten)
        }
    }
}
function Tat([switch]$TatHan) {
    Ghi '-- TAT --'
    if ($CH.TruocKhiTat) { & $CH.TruocKhiTat }
    ChupTienTrinh
    TatThanhPhan $GIAMSAT          # giam sat TRUOC - khong thi no bat lai
    DonSot                         # giam sat / ban cu kieu TURN_ON / WATCHDOG
    $tps = @($CH.ThanhPhan)
    [array]::Reverse($tps)
    foreach ($tp in $tps) {
        ChupTienTrinh
        TatThanhPhan $tp
        if ($TatHan -and $tp.KhiTatHan) { & $tp.KhiTatHan }
    }
    Start-Sleep -Seconds 2
    DonSot
    Start-Sleep -Seconds 1
    # kiem tat sach
    ChupTienTrinh
    $sach = $true
    foreach ($tp in (@($GIAMSAT) + @($CH.ThanhPhan))) {
        $ds = NguoiNghe $tp.Cong
        if ($null -eq $ds) { Hong ('{0}: KHONG DO DUOC cong {1}' -f $tp.Ten, $tp.Cong); $sach = $false; continue }
        foreach ($ma in $ds) {
            $k = CuaDonVi $tp $ma
            if ($k -eq 'cua' -or $k -eq 'khongdoc') { Hong ('{0}: VAN CON PID {1} giu cong {2}' -f $tp.Ten, $ma, $tp.Cong); $sach = $false }
        }
        foreach ($p in (TimTienTrinh $tp)) { Hong ('{0}: VAN CON PID {1} {2}' -f $tp.Ten, $p.ProcessId, $p.Name); $sach = $false }
    }
    if ($sach) { Ghi '  => DA TAT SACH' }
    return $sach
}

# ---------------------------------------------------------------- bat
function BatThanhPhan($tp) {
    $luu = @{}
    if ($tp.Env) {
        foreach ($k in $tp.Env.Keys) {
            $luu[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
            [Environment]::SetEnvironmentVariable($k, $tp.Env[$k], 'Process')
        }
    }
    try {
        $loiRa = '2>&1'
        if ($tp.LogLoi) { $loiRa = '2>> "' + $tp.LogLoi + '"' }
        $arg = '/d /s /c "' + $tp.Lenh + ' >> "' + $tp.Log + '" ' + $loiRa + '"'
        Start-Process -FilePath $CMD -ArgumentList $arg -WorkingDirectory $tp.ThuMuc -WindowStyle Hidden -ErrorAction Stop
        Ghi ('  {0}: DA KHOI CHAY (cong {1}, log {2})' -f $tp.Ten, $tp.Cong, $tp.Log)
        return $true
    } catch {
        Hong ('{0}: khong khoi chay duoc: {1}' -f $tp.Ten, $_.Exception.Message)
        return $false
    } finally {
        foreach ($k in $luu.Keys) { [Environment]::SetEnvironmentVariable($k, $luu[$k], 'Process') }
    }
}
function DamBaoGiamSat {
    $ds = NguoiNghe $GIAMSAT.Cong
    if ($ds -and $ds.Count -gt 0) {
        ChupTienTrinh
        $k = CuaDonVi $GIAMSAT $ds[0]
        if ($k -eq 'khac') { Hong ('Giam sat: cong khoa {0} do UNG DUNG KHAC giu (PID {1})' -f $GIAMSAT.Cong, $ds[0]); return }
        Ghi ('  Giam sat: dang chay san (PID {0})' -f $ds[0]) -Thuong
        return
    }
    $arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -CauHinh "' + $CauHinh + '" -Lenh giamsat'
    try { Start-Process -FilePath $PSEXE -ArgumentList $arg -WorkingDirectory $GOC -WindowStyle Hidden -ErrorAction Stop }
    catch { Hong ('Giam sat: khong khoi chay duoc: ' + $_.Exception.Message); return }
    if (ChoCong $GIAMSAT 25) { Ghi ('  Giam sat: DA KHOI CHAY (cong khoa {0}, moi {1}s)' -f $GIAMSAT.Cong, $CH.ChuKyGiamSat) }
    else { Hong ('Giam sat: khong giu duoc cong khoa {0} sau 25s' -f $GIAMSAT.Cong) }
}
function Bat([switch]$TuGiamSat) {
    Ghi '-- BAT --' -Thuong
    if (-not (KiemDieuKien)) { Hong 'chua du dieu kien - KHONG bat'; return }
    if ($CH.TruocKhiBat) { & $CH.TruocKhiBat }
    $vuaBat = @()
    foreach ($tp in @($CH.ThanhPhan)) {
        ChupTienTrinh
        $ds = NguoiNghe $tp.Cong
        if ($null -eq $ds) { Hong ('{0}: KHONG DO DUOC cong {1}' -f $tp.Ten, $tp.Cong); continue }
        if ($ds.Count -gt 0) {
            $k = CuaDonVi $tp $ds[0]
            if ($k -eq 'khac') { Hong ('{0}: cong {1} do UNG DUNG KHAC giu (PID {2} {3})' -f $tp.Ten, $tp.Cong, $ds[0], $script:TT[$ds[0]].Name); continue }
            Ghi ('  {0}: dang chay san (PID {1}, phien {2})' -f $tp.Ten, $ds[0], $script:TT[$ds[0]].SessionId) -Thuong
            continue
        }
        # cong trong ma co tien trinh cung dau hieu: dang khoi dong (doi) hay treo / ban cu (giet)
        # KHONG boc @(...): ham tra ", $kq" -> @() se thanh mang 1 phan tu RONG (bay da dinh 07/10)
        $dang = TimTienTrinh $tp
        if ($dang.Count -gt 0) {
            $tre = @($dang | Where-Object { (TuoiGiay $_) -lt 90 })
            if ($tre.Count -gt 0) { Ghi ('  {0}: dang khoi dong (PID {1}) - doi' -f $tp.Ten, $tre[0].ProcessId) -Thuong; $vuaBat += $tp; continue }
            foreach ($p in $dang) { GietCay (GocCay ([int]$p.ProcessId) $tp) ('{0}: >90s khong giu cong (treo/ban cu)' -f $tp.Ten) }
            Start-Sleep -Seconds 1
        }
        if ($TuGiamSat -and $tp.KhongHoiSinh -and (& $tp.KhongHoiSinh)) { continue }   # tat chu dong
        if ($tp.DieuKien -and -not (& $tp.DieuKien)) {
            Ghi ('  {0}: chua bat - {1} (lan sau thu lai)' -f $tp.Ten, $tp.DieuKienMoTa)
            if (-not $TuGiamSat) { Hong ('{0}: {1}' -f $tp.Ten, $tp.DieuKienMoTa) }
            continue
        }
        if ($TuGiamSat) { Ghi ('  {0}: CHET -> bat lai' -f $tp.Ten) }
        if (BatThanhPhan $tp) { $vuaBat += $tp }
    }
    foreach ($tp in $vuaBat) {
        $cho = 40
        if ($tp.ChoGiay) { $cho = [int]$tp.ChoGiay }
        if (-not (ChoCong $tp $cho)) { Hong ('{0}: khong nghe cong {1} sau {2}s - xem {3}' -f $tp.Ten, $tp.Cong, $cho, $tp.Log) }
    }
    if (-not $TuGiamSat) {
        foreach ($tp in @($CH.ThanhPhan)) { [void](KiemHttp $tp) }
        DamBaoGiamSat
    }
}

# ---------------------------------------------------------------- bang trang thai
function BangTrangThai {
    ChupTienTrinh
    Ghi '-- TRANG THAI --'
    foreach ($tp in (@($CH.ThanhPhan) + @($GIAMSAT))) {
        $ds = NguoiNghe $tp.Cong
        if ($null -eq $ds) { Hong ('{0,-10} cong {1,-5} KHONG DO DUOC' -f $tp.Ten, $tp.Cong); continue }
        if ($ds.Count -eq 0) { Hong ('{0,-10} cong {1,-5} KHONG CHAY' -f $tp.Ten, $tp.Cong); continue }
        $p = $script:TT[$ds[0]]
        $k = CuaDonVi $tp $ds[0]
        $nhan = switch ($k) { 'cua' { '' } 'khongdoc' { ' (muc Admin)' } 'khac' { ' <== UNG DUNG KHAC' } default { '' } }
        if ($k -eq 'khac') { $script:Loi++ }
        $dau = '[OK ]'
        if ($k -eq 'khac') { $dau = '[LOI]' }
        Ghi ('  {0} {1,-10} cong {2,-5} PID {3,-6} {4,-14} phien {5} tu {6:dd/MM HH:mm:ss}{7}' -f $dau, $tp.Ten, $tp.Cong, $ds[0], $p.Name, $p.SessionId, $p.CreationDate, $nhan)
    }
}

# ---------------------------------------------------------------- chay qua tac vu
function TacVuDangChay {
    $o = & schtasks.exe /Query /TN $CH.TacVu /FO CSV /NH 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $o) { return $null }
    return ([string]$o -match '"Running"')
}
function GuiYeuCau([string]$lenhGui) {
    $id = (Get-Date -Format 'HHmmss') + '_' + ([guid]::NewGuid().ToString('N').Substring(0, 6))
    $dangChay = TacVuDangChay
    if ($dangChay) {
        Write-Host ('Dang co mot luot {0} khac chay - doi no xong...' -f $TEN)
        for ($i = 0; $i -lt 120 -and (TacVuDangChay); $i++) { Start-Sleep -Seconds 3 }
    }
    $yc = Join-Path $YCDIR ('yeucau_' + $id + '.json')
    @{ id = $id; lenh = $lenhGui; luc = (Get-Date).ToString('s'); boi = $env:USERNAME } | ConvertTo-Json | Set-Content -LiteralPath $yc -Encoding ASCII
    $part = Join-Path $YCDIR ('ketqua_' + $id + '.part')
    $xong = Join-Path $YCDIR ('ketqua_' + $id + '.txt')
    $quaTacVu = ($null -ne $dangChay)
    if ($quaTacVu) {
        & schtasks.exe /Run /TN $CH.TacVu 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $quaTacVu = $false }
    }
    if (-not $quaTacVu) {
        Write-Host ('Chua dung duoc tac vu {0} (chay CAU_HINH_TU_HOI_PHUC.bat de dang ky) - xin quyen Admin truc tiep, cua so UAC se hien...' -f $CH.TacVu)
        $arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -CauHinh "' + $CauHinh + '" -Lenh yeucau'
        try { Start-Process -FilePath $PSEXE -ArgumentList $arg -Verb RunAs -WindowStyle Hidden -ErrorAction Stop }
        catch { Remove-Item -LiteralPath $yc -Force -ErrorAction SilentlyContinue; Write-Host 'UAC bi tu choi - KHONG lam gi ca.'; return 1 }
    }
    Write-Host ('[{0}] Da giao viec "{1}" (ma {2}) cho tien trinh muc Admin - dang theo doi...' -f $TEN, $lenhGui, $id)
    $daIn = 0
    $het = (Get-Date).AddMinutes(8)
    $goiLai = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $het) {
        $f = $null
        if (Test-Path -LiteralPath $xong) { $f = $xong } elseif (Test-Path -LiteralPath $part) { $f = $part }
        if ($f) {
            $dong = @(Get-Content -LiteralPath $f -ErrorAction SilentlyContinue)
            for ($i = $daIn; $i -lt $dong.Count; $i++) { Write-Host $dong[$i] }
            $daIn = $dong.Count
            if ($f -eq $xong) {
                $cuoi = $dong | Where-Object { $_ -match '^KET QUA' } | Select-Object -Last 1
                if ($cuoi -match 'KET QUA: OK') { return 0 }
                if ($cuoi -match '(\d+) LOI') { return [int]$matches[1] }
                return 1
            }
        } elseif ($quaTacVu -and (Get-Date) -gt $goiLai -and (Test-Path -LiteralPath $yc)) {
            # yeu cau chua ai nhan (tac vu vua ket thuc dung luc ghi) -> kich lai
            & schtasks.exe /Run /TN $CH.TacVu 2>&1 | Out-Null
            $goiLai = (Get-Date).AddSeconds(15)
        }
        Start-Sleep -Milliseconds 800
    }
    Write-Host ('[LOI] Qua 8 phut chua co ket qua - xem {0}' -f $LOGFILE)
    return 1
}
function XuLyYeuCau {
    # xu ly het yeu cau dang cho (ca yeu cau den trong luc dang chay - tac vu IgnoreNew)
    for ($vong = 0; $vong -lt 20; $vong++) {
        $f = Get-ChildItem -LiteralPath $YCDIR -Filter 'yeucau_*.json' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
        if (-not $f) { break }
        try { $yc = Get-Content -LiteralPath $f.FullName -Raw | ConvertFrom-Json } catch { $yc = $null }
        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
        if (-not $yc -or @('reset', 'bat', 'tat', 'giamsat') -notcontains $yc.lenh) { continue }
        $script:TepBaoCao = Join-Path $YCDIR ('ketqua_' + $yc.id + '.part')
        $script:Loi = 0
        ThucHien $yc.lenh
        $script:TepBaoCao = $null
        Move-Item -LiteralPath (Join-Path $YCDIR ('ketqua_' + $yc.id + '.part')) -Destination (Join-Path $YCDIR ('ketqua_' + $yc.id + '.txt')) -Force
    }
    Get-ChildItem -LiteralPath $YCDIR -Filter 'ketqua_*' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-2) } | Remove-Item -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- khoa noi bo
function LayKhoa([int]$giay) {
    if (-not $script:Mtx) { $script:Mtx = New-Object System.Threading.Mutex($false, ('Global\KimHanh2_VanHanh_' + $TEN)) }
    try { return $script:Mtx.WaitOne($giay * 1000) }
    catch {
        # tien trinh giu khoa bi giet giua chung -> khoa "bi bo roi": ta da nam khoa
        $e = $_.Exception
        while ($e) { if ($e -is [System.Threading.AbandonedMutexException]) { return $true }; $e = $e.InnerException }
        return $false
    }
}
function TraKhoa { try { $script:Mtx.ReleaseMutex() } catch { } }

# ---------------------------------------------------------------- dieu phoi
function ThucHien([string]$l) {
    $tieuDe = @{ reset = 'RESET'; bat = 'BAT'; tat = 'TAT HAN'; giamsat = 'BAT GIAM SAT' }[$l]
    Ghi ('===== {0} {1} - {2:dd/MM/yyyy HH:mm:ss} - {3} =====' -f $tieuDe, $TEN, (Get-Date), $env:USERNAME)
    if (-not (LayKhoa 300)) { Hong 'dang co luot van hanh khac giu khoa qua 5 phut - bo qua'; Ghi ('KET QUA: {0} LOI' -f $script:Loi); return }
    try {
        switch ($l) {
            'reset' {
                Ghi '-- KIEM DIEU KIEN TRUOC KHI TAT --'
                if (-not (KiemDieuKien)) { Hong 'chua du dieu kien de bat lai - KHONG tat gi ca, he giu nguyen' }
                else {
                    $sach = Tat
                    if (-not $sach) { Hong 'tat KHONG sach - van bat phan con thieu de khong bo he chet, nhung CON TIEN TRINH CU (co the chay ma .py CU)' }
                    Start-Sleep -Seconds 2
                    Bat
                    Start-Sleep -Seconds 1
                    BangTrangThai
                }
            }
            'bat' { Bat; BangTrangThai }
            'tat' { [void](Tat -TatHan); Ghi '  (giam sat da tat - khong ai tu bat lai cho toi lan bat/reset/khoi dong may sau)' }
            'giamsat' { DamBaoGiamSat }
        }
    } finally { TraKhoa }
    if ($script:Loi -eq 0) { Ghi 'KET QUA: OK' } else { Ghi ('KET QUA: {0} LOI' -f $script:Loi) }
}
function VongGiamSat {
    Add-Type -Namespace KH2 -Name K32 -MemberDefinition '[DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetHandleInformation(System.IntPtr h, int mask, int flags);' -ErrorAction SilentlyContinue
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, [int]$CH.CongGiamSat)
    $l.ExclusiveAddressUse = $true
    try { $l.Start() } catch { exit 0 }   # da co vong giam sat khac giu cong khoa
    try { [void][KH2.K32]::SetHandleInformation($l.Server.Handle, 1, 0) } catch { }
    $script:Im = $true
    Ghi ('giam sat BAT DAU (cong khoa {0}, moi {1}s, phien {2})' -f $CH.CongGiamSat, $CH.ChuKyGiamSat, $PHIEN)
    while ($true) {
        Start-Sleep -Seconds ([int]$CH.ChuKyGiamSat)
        if (-not (LayKhoa 0)) { continue }   # dang reset/bat - nhuong
        try { $script:Loi = 0; Bat -TuGiamSat } catch { Ghi ('giam sat loi: ' + $_.Exception.Message) } finally { TraKhoa }
    }
}

switch ($Lenh) {
    'kiem' {
        Ghi ('===== KIEM {0} - {1:dd/MM/yyyy HH:mm:ss} =====' -f $TEN, (Get-Date))
        if (-not (LaAdmin)) { Ghi '  (quyen thuong: chi doc duoc cong + PID, khong doc duoc dong lenh tien trinh muc Admin)' }
        BangTrangThai
        foreach ($tp in @($CH.ThanhPhan)) { [void](KiemHttp $tp) }
        if ($script:Loi -eq 0) { Ghi 'KET QUA: OK' } else { Ghi ('KET QUA: {0} LOI' -f $script:Loi) }
        exit $script:Loi
    }
    'giamsat' { VongGiamSat; exit 0 }
    'yeucau' { XuLyYeuCau; exit 0 }
    default {
        # reset / bat / tat: chi lam TAI CHO khi la Admin O PHIEN NEN (session 0 - tac vu luc
        # khoi dong may / tac vu van hanh). Moi noi goi khac -> giao cho tac vu: moi tien trinh
        # cua he luon nam o phien nen, song qua dang xuat, cung muc quyen voi nhau.
        if ((LaAdmin) -and $PHIEN -eq 0) { ThucHien $Lenh; exit $script:Loi }
        $rc = GuiYeuCau $Lenh
        # Luot DANG NHAP (Admin, co man hinh): keo thanh phan uu tien phien nguoi dung ve day
        if ((LaAdmin) -and $PHIEN -ne 0 -and @('bat', 'reset') -contains $Lenh) {
            foreach ($tp in @($CH.ThanhPhan)) {
                if (-not $tp.UuTienPhienNguoiDung) { continue }
                ChupTienTrinh
                $ds = NguoiNghe $tp.Cong
                if (-not $ds -or $ds.Count -eq 0) { continue }
                if ($script:TT[$ds[0]].SessionId -ne 0 -or (CuaDonVi $tp $ds[0]) -ne 'cua') { continue }
                if (-not (LayKhoa 60)) { break }
                try {
                    Ghi ('  {0}: dang o phien nen -> chuyen sang phien nguoi dung {1}' -f $tp.Ten, $PHIEN)
                    TatThanhPhan $tp 'chuyen phien'
                    Start-Sleep -Seconds 2
                    if (BatThanhPhan $tp) { if (-not (ChoCong $tp 40)) { Hong ('{0}: khong len lai sau khi chuyen phien' -f $tp.Ten) } }
                } finally { TraKhoa }
            }
            if ($script:Loi -gt 0) { $rc += $script:Loi }
        }
        exit $rc
    }
}
