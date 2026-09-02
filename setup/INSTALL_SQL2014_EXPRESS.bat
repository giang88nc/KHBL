@echo off
REM ============================================================
REM  INSTALL_SQL2014_EXPRESS.bat - CHAY BANG "Run as Administrator"
REM  Cai SQL Server 2014 SP3 Express (instance SQL2014) tren may Mr Giang
REM  - ban MOI NHAT con restore duoc backup tu SQL 2005 (PMV @ PC KK).
REM  Sau khi cai xong: mo D:\PYTHON\KHBL\.env, dat
REM      PMV_LOCAL_MSSQL=localhost\SQL2014
REM  roi tu do job dem se tu restore sandbox PMV_SANDBOX (khi da co share keo .bak ve).
REM  Neu bao loi thieu .NET 3.5: bat Windows Feature ".NET Framework 3.5" truoc.
REM ============================================================
cd /d %~dp0
net session >nul 2>&1
if not %errorlevel%==0 (
    echo LOI: phai chay file nay bang Run as Administrator.
    pause
    exit /b 1
)
if not exist SQLEXPR_x64_ENU.exe (
    echo LOI: thieu SQLEXPR_x64_ENU.exe canh file nay.
    pause
    exit /b 1
)
echo Dang cai SQL Server 2014 Express (instance SQL2014) - cho vai phut...
SQLEXPR_x64_ENU.exe /Q /IACCEPTSQLSERVERLICENSETERMS /ACTION=Install /FEATURES=SQLEngine /INSTANCENAME=SQL2014 /SQLSYSADMINACCOUNTS="%COMPUTERNAME%\%USERNAME%" "BUILTIN\Administrators" /SQLSVCSTARTUPTYPE=Automatic /BROWSERSVCSTARTUPTYPE=Automatic
if %errorlevel%==0 (
    echo.
    echo CAI XONG. Kiem tra: sc query MSSQL$SQL2014
    echo Buoc tiep: sua .env  PMV_LOCAL_MSSQL=localhost\SQL2014
) else (
    echo.
    echo CAI LOI - xem log tai %%ProgramFiles%%\Microsoft SQL Server\120\Setup Bootstrap\Log\Summary.txt
)
pause
