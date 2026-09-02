@echo off
REM ============================================================
REM  TURN_ON_KHBL.bat - Bat he thong KHBL (3 tien trinh AN)
REM    1) Web (waitress) : http://0.0.0.0:8100 (log: logs\server.log)
REM    2) Scheduler      : manage.py run_scheduler (log: logs\scheduler.log)
REM    3) Watchdog       : WATCHDOG_KHBL.bat - 60s/lan tu goi lai file nay
REM  Chong bat trung tung tien trinh - goi lai bao nhieu lan cung an toan.
REM  KHONG goi tu Git Bash (bai hoc KHJ) - goi qua PowerShell: cmd /c ...
REM ============================================================
setlocal
set PYTHONUTF8=1
cd /d D:\PYTHON\KHBL
if not exist logs mkdir logs

REM --- Cho MySQL80 chay (toi da 60s) ---
set /a TRIES=0
:wait_mysql
sc query MySQL80 | find "RUNNING" >nul 2>&1
if %errorlevel%==0 goto mysql_ok
set /a TRIES+=1
if %TRIES% geq 12 (
    echo [KHBL] LOI: MySQL80 khong chay sau 60s. Huy bat he thong.
    exit /b 1
)
ping -n 6 127.0.0.1 >nul
goto wait_mysql
:mysql_ok

REM --- 1) WEB: port 8100 chua co ai nghe thi moi bat (waitress) ---
netstat -ano | findstr "LISTENING" | findstr ":8100 " >nul 2>&1
if %errorlevel%==0 (
    echo [KHBL] Web DA CHAY san tren port 8100. Bo qua.
) else (
    echo [KHBL] Dang bat web waitress...
    wscript //B "%~dp0run_hidden_khbl.vbs" "venv\Scripts\python.exe -m waitress --listen=*:8100 --threads=4 config.wsgi:application >> logs\server.log 2>&1"
)

REM --- 2) SCHEDULER: chua co tien trinh run_scheduler cua KHBL thi moi bat ---
powershell -NoProfile -Command "exit (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.ExecutablePath -match 'KHBL' -and $_.CommandLine -match 'run_scheduler' }).Count)"
if %errorlevel% gtr 0 (
    echo [KHBL] Scheduler DA CHAY san. Bo qua.
) else (
    echo [KHBL] Dang bat scheduler...
    wscript //B "%~dp0run_hidden_khbl.vbs" "set PYTHONUTF8=1&& venv\Scripts\python.exe manage.py run_scheduler >> logs\scheduler.log 2>&1"
)

REM --- 3) WATCHDOG: chua chay thi bat (tu goi lai TURN_ON moi 60s) ---
powershell -NoProfile -Command "exit (@(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match 'WATCHDOG_KHBL' }).Count)"
if %errorlevel% gtr 0 (
    echo [KHBL] Watchdog DA CHAY san. Bo qua.
) else (
    echo [KHBL] Dang bat watchdog...
    wscript //B "%~dp0run_hidden_khbl.vbs" "%~dp0WATCHDOG_KHBL.bat"
)

REM --- Kiem tra web len chua (toi da 30s) ---
set /a TRIES=0
:wait_web
ping -n 3 127.0.0.1 >nul
netstat -ano | findstr "LISTENING" | findstr ":8100 " >nul 2>&1
if %errorlevel%==0 (
    echo [KHBL] HOAN TAT: web http://localhost:8100 + scheduler + watchdog dang chay.
    exit /b 0
)
set /a TRIES+=1
if %TRIES% lss 15 goto wait_web
echo [KHBL] LOI: web chua len sau 30s - xem logs\server.log
exit /b 1
