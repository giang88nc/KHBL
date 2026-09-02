@echo off
REM ============================================================
REM  WATCHDOG_KHBL.bat - Vong lap canh gac, KHONG chay truc tiep
REM  (TURN_ON_KHBL.bat tu bat file nay). Moi 60s goi lai TURN_ON:
REM  web/scheduler chet vi bat ky ly do gi se duoc bat lai ngay.
REM ============================================================
:loop
ping -n 61 127.0.0.1 >nul
call "%~dp0TURN_ON_KHBL.bat" >nul 2>&1
goto loop
