@echo off
REM ============================================================
REM  TURN_OFF_KHBL.bat - Tat he thong KHBL
REM    0) Watchdog  : tat TRUOC (khong thi 60s sau no bat lai)
REM    1) Caddy     : tien trinh caddy.exe co dong lenh chua ...\PYTHON\KHBL\... (Caddy RIENG cua KHBL,
REM                   KHONG dung Caddy cua KIMHANH) + bat ky ai dang nghe :8100 / :8101
REM    2) Web       : waitress 127.0.0.1:8101, tat ca cay tien trinh
REM    3) Scheduler : tim theo dong lenh run_scheduler cua KHBL
REM ============================================================
setlocal
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
echo [KHBL] Da tat he thong KHBL.
exit /b 0
