@echo off
REM ============================================================
REM  TURN_ON_KHBL.bat - Bat he thong KHBL (4 tien trinh AN)
REM    1) Web (waitress) : 127.0.0.1:8101 NOI BO (log: logs\server.log)
REM    2) Caddy HTTPS    : *:8100 - http+https cung cong, http tu nhay https (log: logs\caddy.log)
REM                        https://tiemvangkimhanh2:8100 = https://localhost:8100 = https://192.168.1.6:8100
REM    3) Scheduler      : manage.py run_scheduler (log: logs\scheduler.log)
REM    4) Watchdog       : WATCHDOG_KHBL.bat - 60s/lan tu goi lai file nay
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

REM --- 1) WEB waitress NOI BO 127.0.0.1:8101 (08/09/2026: Caddy dung truoc, waitress khong con nghe LAN) ---
REM     findstr mac dinh hieu [ ] la regex -> /L (literal) + /C: cho tung chuoi.
netstat -ano | findstr "LISTENING" | findstr /L /C:"127.0.0.1:8101 " >nul 2>&1
if %errorlevel%==0 (
    echo [KHBL] Web waitress DA CHAY san tren 127.0.0.1:8101. Bo qua.
) else (
    echo [KHBL] Dang bat web waitress 127.0.0.1:8101...
    wscript //B "%~dp0run_hidden_khbl.vbs" "venv\Scripts\python.exe -m waitress --listen=127.0.0.1:8101 --threads=8 config.wsgi:application >> logs\server.log 2>&1"
)

REM --- 2) CADDY HTTPS *:8100 (CA dung chung KIMHANH: root.crt/root.key copy sang runtime neu chua co) ---
if not exist "runtime\caddy-data\pki\authorities\local" mkdir "runtime\caddy-data\pki\authorities\local"
if not exist "runtime\caddy-data\pki\authorities\local\root.key" (
    if exist "D:\PYTHON\KIMHANH\runtime\caddy-data\pki\authorities\local\root.key" (
        echo [KHBL] Chep CA noi bo dung chung tu KIMHANH sang runtime\caddy-data ...
        copy /Y "D:\PYTHON\KIMHANH\runtime\caddy-data\pki\authorities\local\root.crt" "runtime\caddy-data\pki\authorities\local\" >nul
        copy /Y "D:\PYTHON\KIMHANH\runtime\caddy-data\pki\authorities\local\root.key" "runtime\caddy-data\pki\authorities\local\" >nul
    )
)
REM     Listener CHI loopback tren 8100 (waitress cu / chay tay) thi kill - cong 8100 nay la cua Caddy.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr /L /C:"127.0.0.1:8100 "') do (
    echo [KHBL] Cong 8100 dang bi tien trinh loopback PID %%p chiem - kill de Caddy bat *:8100
    taskkill /PID %%p /F >nul 2>&1
)
netstat -ano | findstr "LISTENING" | findstr /L /C:"0.0.0.0:8100 " /C:"[::]:8100 " >nul 2>&1
if %errorlevel%==0 (
    echo [KHBL] Caddy HTTPS DA CHAY san tren port 8100. Bo qua.
) else (
    if not exist "ops\caddy\caddy.exe" (
        echo [KHBL] LOI: thieu ops\caddy\caddy.exe - copy tu D:\PYTHON\KIMHANH\ops\caddy\caddy.exe
    ) else (
        echo [KHBL] Dang bat Caddy HTTPS *:8100...
        REM duong dan TUYET DOI de TURN_OFF nhan dien Caddy cua KHBL qua CommandLine (chua PYTHON\KHBL)
        wscript //B "%~dp0run_hidden_khbl.vbs" "D:\PYTHON\KHBL\ops\caddy\caddy.exe run --config D:\PYTHON\KHBL\ops\caddy\Caddyfile --adapter caddyfile >> logs\caddy.log 2>&1"
    )
)

REM --- 3) SCHEDULER: chua co tien trinh run_scheduler cua KHBL thi moi bat ---
REM     (venv = cap cha-con: cha ...\KHBL\venv\...python.exe, con D:\PYTHON\python.exe
REM      khong mang ten du an - nhan dien con qua ParentProcessId tro ve cha KHBL)
powershell -NoProfile -Command "$s=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run_scheduler' }); $l=@($s | Where-Object { $_.ExecutablePath -like '*\PYTHON\KHBL\*' }); exit @($s | Where-Object { $_.ExecutablePath -like '*\PYTHON\KHBL\*' -or $l.ProcessId -contains $_.ParentProcessId }).Count"
if %errorlevel% gtr 0 (
    echo [KHBL] Scheduler DA CHAY san. Bo qua.
) else (
    echo [KHBL] Dang bat scheduler...
    wscript //B "%~dp0run_hidden_khbl.vbs" "set PYTHONUTF8=1&& venv\Scripts\python.exe manage.py run_scheduler >> logs\scheduler.log 2>&1"
)

REM --- 4) WATCHDOG: chua chay thi bat (tu goi lai TURN_ON moi 60s) ---
powershell -NoProfile -Command "exit (@(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match 'WATCHDOG_KHBL' }).Count)"
if %errorlevel% gtr 0 (
    echo [KHBL] Watchdog DA CHAY san. Bo qua.
) else (
    echo [KHBL] Dang bat watchdog...
    wscript //B "%~dp0run_hidden_khbl.vbs" "%~dp0WATCHDOG_KHBL.bat"
)

REM --- Kiem tra web + Caddy len chua (toi da 30s) ---
set /a TRIES=0
:wait_web
ping -n 3 127.0.0.1 >nul
netstat -ano | findstr "LISTENING" | findstr /L /C:"127.0.0.1:8101 " >nul 2>&1
if not %errorlevel%==0 goto wait_more
netstat -ano | findstr "LISTENING" | findstr /L /C:"0.0.0.0:8100 " /C:"[::]:8100 " >nul 2>&1
if %errorlevel%==0 (
    echo [KHBL] HOAN TAT: https://tiemvangkimhanh2:8100 ^(Caddy *:8100 -^> waitress 127.0.0.1:8101^) + scheduler + watchdog dang chay.
    exit /b 0
)
:wait_more
set /a TRIES+=1
if %TRIES% lss 15 goto wait_web
echo [KHBL] LOI: web/Caddy chua len sau 30s - xem logs\server.log va logs\caddy.log
exit /b 1
