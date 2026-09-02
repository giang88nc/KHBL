@echo off
REM RESET_KHBL.bat - Tat roi bat lai he thong KHBL (sau khi sua file .py)
call "%~dp0TURN_OFF_KHBL.bat"
ping -n 4 127.0.0.1 >nul
call "%~dp0TURN_ON_KHBL.bat"
