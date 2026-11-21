<#
.SYNOPSIS
  Run the Audaxity backend for phone testing.

.DESCRIPTION
  `--proxy-headers` with `--forwarded-allow-ips` matters once a Cloudflare
  tunnel is in front: without them uvicorn sees the tunnel's connection as
  plain HTTP from 127.0.0.1 and reports `http://` scheme and the proxy's IP
  on every request. That makes request logs misleading and breaks anything
  that builds an absolute URL from the incoming request.

  `--host 0.0.0.0` is what lets the phone connect over Wi-Fi at all; bound to
  127.0.0.1 the backend is unreachable from any other device, which is the
  first thing to check when the app says "unreachable".
#>
[CmdletBinding()]
param(
    [int]$Port = 8010,
    [string]$DataDir = './data_phone'
)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..' 'backend')

$env:DATA_DIR = $DataDir

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $Port is already in use by PID $($busy[0].OwningProcess)." -ForegroundColor Red
    Write-Host 'Pick another port rather than killing it - it may not be ours.' -ForegroundColor Yellow
    exit 1
}

$lan = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1).IPAddress

Write-Host "Backend on http://${lan}:$Port  (data: $DataDir)" -ForegroundColor Green
Write-Host "Put that address in the app under Settings -> Server address." -ForegroundColor Yellow
Write-Host ''

& .venv/Scripts/python -m uvicorn app.main:app `
    --host 0.0.0.0 --port $Port `
    --proxy-headers --forwarded-allow-ips '*'
