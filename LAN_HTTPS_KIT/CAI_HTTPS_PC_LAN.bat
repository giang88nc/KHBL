@echo off
title Kim Hanh 2 - Cai HTTPS cho PC LAN (tiemvangkimhanh2)
REM ============================================================
REM  Chay 1 LAN tren moi PC LAN (chuot phai -> Run as administrator, hoac cu chay:
REM  script tu xin quyen Admin). Lam 3 viec:
REM    1) hosts : 192.168.1.6  tiemvangkimhanh2
REM    2) tin CA noi bo Kim Hanh (file kimhanh-lan-root-ca.crt cung thu muc)
REM    3) mo https://tiemvangkimhanh2:8100/banle/ban-hang/
REM  Copy NGUYEN THU MUC LAN_HTTPS_KIT sang PC roi chay file nay.
REM ============================================================
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cai-https-khbl.ps1"
if errorlevel 1 (
  echo.
  echo CAI DAT THAT BAI - chup man hinh gui quan tri vien.
) else (
  echo.
  echo XONG. Dong het cua so trinh duyet roi mo lai: https://tiemvangkimhanh2:8100
)
pause
