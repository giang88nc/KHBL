param([switch]$Elevated)
# Kim Hanh 2 - cai HTTPS LAN cho KHBL (08/09/2026). Chay qua CAI_HTTPS_PC_LAN.bat.
# Tu xin quyen Administrator (UAC) vi phai ghi hosts + nap CA vao Trusted Root cua may.
$ErrorActionPreference = "Stop"
$ServerIp = "192.168.1.6"
$Domain   = "tiemvangkimhanh2"
$Port     = "8100"
$ThisScript = $MyInvocation.MyCommand.Path
$CertFile = Join-Path $PSScriptRoot "kimhanh-lan-root-ca.crt"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) {
    if ($Elevated) { throw "Khong duoc cap quyen Administrator." }
    $args2 = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $ThisScript), "-Elevated")
    $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $args2 -Wait -PassThru
    exit $p.ExitCode
}

# --- 1) hosts: 192.168.1.6  tiemvangkimhanh2 (thay dong cu neu IP khac) ---
$hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$lines = @()
if (Test-Path -LiteralPath $hosts) { $lines = Get-Content -LiteralPath $hosts -ErrorAction Stop }
$kept = @($lines | Where-Object { $_ -notmatch ("^\s*\S+\s+" + [regex]::Escape($Domain) + "\s*(#.*)?$") })
$kept += ("{0}`t{1}" -f $ServerIp, $Domain)
# Ghi hosts bang .NET, bo tam thuoc tinh Hidden/ReadOnly/System roi tra lai:
# PS 5.1 Set-Content len file Hidden/ReadOnly no "Stream was not readable" (loi da gap 09/09/2026 tren PC LAN -
# thuong do phan mem diet virus dat thuoc tinh 'bao ve hosts').
$thuocTinhCu = $null
if (Test-Path -LiteralPath $hosts) {
    $thuocTinhCu = (Get-Item -LiteralPath $hosts -Force).Attributes
    [IO.File]::SetAttributes($hosts, [IO.FileAttributes]::Normal)
}
try {
    $ok = $false
    for ($lan = 1; $lan -le 4 -and -not $ok; $lan++) {
        try { [IO.File]::WriteAllText($hosts, (($kept -join "`r`n") + "`r`n"), [Text.Encoding]::ASCII); $ok = $true }
        catch { if ($lan -eq 4) { throw }; Start-Sleep -Milliseconds 700 }   # file dang bi diet virus giu tam -> thu lai
    }
} catch {
    throw ("Khong ghi duoc file hosts ({0}). Neu may co phan mem diet virus (Bkav/Kaspersky/Avast...) dang 'bao ve hosts', " +
           "tam tat bao ve hoac them ngoai le cho PowerShell roi chay lai. Chi tiet: {1}") -f $hosts, $_.Exception.Message
} finally {
    if ($null -ne $thuocTinhCu) {
        try { [IO.File]::SetAttributes($hosts, $thuocTinhCu) } catch {}
    }
}
ipconfig /flushdns | Out-Null
Write-Host ("[1/3] hosts: {0} -> {1}" -f $Domain, $ServerIp) -ForegroundColor Green

# --- 2) tin CA noi bo (Trusted Root, ca may). Neu thieu file .crt thi tai tu may chu (bo qua kiem cert). ---
if (-not (Test-Path -LiteralPath $CertFile)) {
    Write-Host "[2/3] Khong thay kimhanh-lan-root-ca.crt canh script - tai tu may chu..." -ForegroundColor Yellow
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri ("https://{0}:{1}/static/cert/kimhanh-lan-root-ca.crt" -f $ServerIp, $Port) -OutFile $CertFile -UseBasicParsing
}
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertFile)
$have = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Thumbprint -eq $cert.Thumbprint
if (-not $have) {
    Import-Certificate -FilePath $CertFile -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
    Write-Host ("[2/3] Da nap CA '{0}' vao Trusted Root." -f $cert.Subject) -ForegroundColor Green
} else {
    Write-Host ("[2/3] CA '{0}' da duoc tin tu truoc (KIMHANH dung chung)." -f $cert.Subject) -ForegroundColor Green
}
# Firefox dung kho rieng -> bat chinh sach dung Trusted Root cua Windows
$ff = "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates"
New-Item -Path $ff -Force | Out-Null
New-ItemProperty -Path $ff -Name "ImportEnterpriseRoots" -Value 1 -PropertyType DWord -Force | Out-Null

# --- 3) kiem + mo trinh duyet ---
$url = "https://{0}:{1}/banle/ban-hang/" -f $Domain, $Port
try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -MaximumRedirection 0 -ErrorAction SilentlyContinue
    Write-Host ("[3/3] Ket noi {0} OK (HTTP {1})" -f $url, $r.StatusCode) -ForegroundColor Green
} catch {
    $code = $null; try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
    if ($code) { Write-Host ("[3/3] Ket noi {0} OK (HTTP {1} - chuyen toi dang nhap)" -f $url, $code) -ForegroundColor Green }
    else { Write-Host ("[3/3] CHUA ket noi duoc {0}: {1}" -f $url, $_.Exception.Message) -ForegroundColor Yellow }
}
Start-Process $url
Write-Host "Xong. Dong het cua so trinh duyet roi mo lai de khoa xanh hien dung." -ForegroundColor Cyan
