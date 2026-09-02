@echo off
REM ============================================================
REM  TURN_OFF_KHBL.bat - Tat he thong KHBL
REM    0) Watchdog  : tat TRUOC (khong thi 60s sau no bat lai)
REM    1) Web       : tim theo port 8100, tat ca cay tien trinh
REM    2) Scheduler : tim theo dong lenh run_scheduler cua KHBL
REM ============================================================
setlocal
powershell -NoProfile -Command "$w = @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match 'WATCHDOG_KHBL' }); if ($w.Count -eq 0) { Write-Host '[KHBL] Watchdog khong chay san.' } else { $w | ForEach-Object { Write-Host ('[KHBL] Tat watchdog PID ' + $_.ProcessId + ' ...'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8100 "') do (
    set FOUND=1
    echo [KHBL] Tat web PID %%p ...
    taskkill /F /T /PID %%p >nul 2>&1
)
if %FOUND%==0 echo [KHBL] Web khong chay san.
powershell -NoProfile -Command "$p = @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.ExecutablePath -match 'KHBL' -and $_.CommandLine -match 'run_scheduler' }); if ($p.Count -eq 0) { Write-Host '[KHBL] Scheduler khong chay san.' } else { $p | ForEach-Object { Write-Host ('[KHBL] Tat scheduler PID ' + $_.ProcessId + ' ...'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
echo [KHBL] Da tat he thong KHBL.
exit /b 0
