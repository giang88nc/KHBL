@echo off
REM ============================================================
REM  TURN_OFF_KHBL.bat - Tat he thong KHBL
REM    0) Watchdog  : tat TRUOC (khong thi 60s sau no bat lai)
REM    1) Caddy     : tien trinh caddy.exe co dong lenh chua ...\PYTHON\KHBL\... (Caddy RIENG cua KHBL,
REM                   KHONG dung Caddy cua KIMHANH) + bat ky ai dang nghe :8100 / :8101
REM    2) Web       : waitress 127.0.0.1:8101, tat ca cay tien trinh
REM    3) Scheduler : tim theo dong lenh run_scheduler cua KHBL
REM  Tu 10/09/2026: he duoc bat tu luc BOOT boi scheduled task chay SYSTEM
REM  (KimHanh2-ToanHeThong-Boot, xem D:\PYTHON\KHJ\KHOI_DONG_TOAN_HE_THONG.bat) -> tien trinh thuoc
REM  SYSTEM, user thuong khong kill duoc. Quy tac: TAT THU -> KIEM TRA -> con sot thi TU NANG QUYEN (UAC).
REM ============================================================
setlocal
call :kill

REM --- kiem tra con sot khong ---
set LEFT=0
netstat -ano | findstr "LISTENING" | findstr /L /C:":8100 " /C:":8101 " >nul 2>&1
if %errorlevel%==0 set LEFT=1
powershell -NoProfile -Command "$s=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run_scheduler' }); $l=@($s | Where-Object { $_.ExecutablePath -like '*\PYTHON\KHBL\*' }); exit @($s | Where-Object { $_.ExecutablePath -like '*\PYTHON\KHBL\*' -or $l.ProcessId -contains $_.ParentProcessId }).Count"
if %errorlevel% gtr 0 set LEFT=1
powershell -NoProfile -Command "exit (@(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match 'WATCHDOG_KHBL' }).Count)"
if %errorlevel% gtr 0 set LEFT=1
if %LEFT%==0 (
    echo [KHBL] Da tat he thong KHBL.
    exit /b 0
)
if /i "%~1"=="--elevated" (
    echo [KHBL] LOI: da nang quyen ma van con tien trinh - kiem tra tay: netstat -ano ^| findstr :8100
    exit /b 1
)
net session >nul 2>&1
if %errorlevel%==0 (
    echo [KHBL] LOI: dang la Admin ma van con tien trinh - kiem tra tay.
    exit /b 1
)
echo [KHBL] Tien trinh do SYSTEM/Admin so huu - xin quyen Admin de tat ^(cua so UAC, bam YES^)...
powershell -NoProfile -Command "try { Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs -Wait -ErrorAction Stop; exit 0 } catch { Write-Host '[KHBL] UAC bi tu choi:' $_.Exception.Message; exit 1 }"
exit /b %errorlevel%

:kill
powershell -NoProfile -Command "$w = @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match 'WATCHDOG_KHBL' }); if ($w.Count -eq 0) { Write-Host '[KHBL] Watchdog khong chay san.' } else { $w | ForEach-Object { Write-Host ('[KHBL] Tat watchdog PID ' + $_.ProcessId + ' ...'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
powershell -NoProfile -Command "$c = @(Get-CimInstance Win32_Process -Filter \"Name='caddy.exe'\" | Where-Object { $_.CommandLine -like '*PYTHON\KHBL*' }); if ($c.Count -eq 0) { Write-Host '[KHBL] Caddy KHBL khong chay san.' } else { $c | ForEach-Object { Write-Host ('[KHBL] Tat Caddy PID ' + $_.ProcessId + ' ...'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr /L /C:":8100 " /C:":8101 "') do (
    set FOUND=1
    echo [KHBL] Tat tien trinh nghe 8100/8101 PID %%p ...
    taskkill /F /T /PID %%p >nul 2>&1
)
if %FOUND%==0 echo [KHBL] Web khong chay san.
REM Tat scheduler CUA KHBL: cha ...\KHBL\venv\... + con qua ParentProcessId - khong dung cham KHJ
powershell -NoProfile -Command "$s=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run_scheduler' }); $l=@($s | Where-Object { $_.ExecutablePath -like '*\PYTHON\KHBL\*' }); $p=@($s | Where-Object { $_.ExecutablePath -like '*\PYTHON\KHBL\*' -or $l.ProcessId -contains $_.ParentProcessId }); if ($p.Count -eq 0) { Write-Host '[KHBL] Scheduler khong chay san.' } else { $p | ForEach-Object { Write-Host ('[KHBL] Tat scheduler PID ' + $_.ProcessId + ' ...'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
ping -n 2 127.0.0.1 >nul
goto :eof
