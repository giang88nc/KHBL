@echo off
REM ============================================================
REM  RESET_KHBL.bat - CUA VAO DUY NHAT van hanh KHBL (07/10/2026, GD chot)
REM    (bam dup, khong tham so)  RESET: kiem dieu kien -> TAT het -> kiem tat sach
REM                              -> BAT lai -> kiem OK (cong + HTTPS + scheduler song)
REM    /reset  nhu tren nhung khong dung cho o cuoi (Claude / lenh goi)
REM    /bat    chi bat cai dang thieu   /tat  tat han (bao tri)   /kiem  chi xem
REM  Don vi KHBL = Caddy HTTPS 8100 + waitress 8101 + scheduler (khoa 8109) + giam sat (khoa 8119).
REM  KHONG dung toi cau noi khach hang 18202 (chay bang venv KHBL nhung THUOC KHCD)
REM  va khong dung toi KHJ / KHCD / NGROK.
REM  Quyen thuong cung bam duoc: tu nho tac vu Windows "KimHanh2-VanHanh-KHBL" (muc Admin,
REM  KHONG hoi UAC, tien trinh nam o phien nen - khong chet theo app/cua so goi).
REM  Dong co: ops\vanhanh\vanhanh.ps1 (ban sao giong het KHJ) + cauhinh_khbl.ps1
REM  Log: logs\vanhanh_khbl.log. Goi tu PowerShell: cmd /c D:\PYTHON\KHBL\RESET_KHBL.bat /reset
REM ============================================================
setlocal
set "LENH=reset"
if /i "%~1"=="/bat" set "LENH=bat"
if /i "%~1"=="/tat" set "LENH=tat"
if /i "%~1"=="/kiem" set "LENH=kiem"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\vanhanh\vanhanh.ps1" -CauHinh "%~dp0ops\vanhanh\cauhinh_khbl.ps1" -Lenh %LENH%
set "KQ=%errorlevel%"
if "%~1"=="" pause
exit /b %KQ%
