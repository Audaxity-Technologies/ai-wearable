<#
.SYNOPSIS
  Expose the local Audaxity backend on a stable HTTPS URL via Cloudflare.

.DESCRIPTION
  Solves two problems at once:

  1. The phone can only reach the backend when it is on the same Wi-Fi, and
     the laptop's LAN address changes (it already went .216 -> .156 during
     device testing, which meant re-typing the address on the phone).

  2. Google OAuth will not accept an `http://` redirect URI for any host
     other than localhost. Without HTTPS, connecting Google Calendar from the
     phone is impossible - not awkward, impossible. The tunnel is what makes
     phase 10 reachable from a real device.

  Two modes:

  * Quick (default): `cloudflared tunnel --url`. No Cloudflare account, no
    DNS. The URL is random and changes every restart, which is fine for an
    afternoon of testing but means re-entering it in the app each time AND
    re-registering the redirect URI with Google each time.

  * Named (-Name): a persistent hostname on a domain you control. Survives
    restarts, so the Google redirect URI stays registered. This is the one to
    use once you are iterating daily.

.EXAMPLE
  ./scripts/tunnel.ps1
  Quick tunnel on port 8010.

.EXAMPLE
  ./scripts/tunnel.ps1 -Name audaxity -Hostname api.example.com
  Persistent tunnel. Requires `cloudflared tunnel login` once first.
#>
[CmdletBinding()]
param(
    [int]$Port = 8010,
    [string]$Name,
    [string]$Hostname
)

$ErrorActionPreference = 'Stop'

# Prefer a copy vendored into tools/ over one on PATH.
#
# winget's MSI download is served from GitHub's release CDN, which resets the
# connection from some networks - it failed repeatedly here while everything
# else downloaded fine. The standalone .exe needs no installer and no admin
# rights, so tools/cloudflared.exe is the supported way to have it.
$local = Join-Path $PSScriptRoot '..' 'tools' 'cloudflared.exe'
if (Test-Path $local) {
    $cloudflared = (Resolve-Path $local).Path
}
elseif (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    $cloudflared = 'cloudflared'
}
else {
    Write-Host 'cloudflared was not found.' -ForegroundColor Yellow
    Write-Host 'Either install it:' -ForegroundColor Yellow
    Write-Host '  winget install --id Cloudflare.cloudflared' -ForegroundColor Cyan
    Write-Host 'or drop the standalone binary at tools\cloudflared.exe:' -ForegroundColor Yellow
    Write-Host '  curl -L -o tools/cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -ForegroundColor Cyan
    exit 1
}

# Fail early if nothing is listening: a tunnel to a dead port returns 502 for
# every request, which looks like a tunnel problem and is not one.
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Write-Host "Nothing is listening on port $Port." -ForegroundColor Red
    Write-Host 'Start the backend first:' -ForegroundColor Yellow
    Write-Host "  cd backend; .venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port $Port" -ForegroundColor Cyan
    exit 1
}

if ($Name) {
    if (-not $Hostname) {
        Write-Host 'A named tunnel needs -Hostname as well.' -ForegroundColor Red
        exit 1
    }

    Write-Host "Named tunnel '$Name' -> https://$Hostname -> localhost:$Port" -ForegroundColor Green
    Write-Host ''
    Write-Host 'If this is the first run, do these once:' -ForegroundColor Yellow
    Write-Host "  $cloudflared tunnel login"
    Write-Host "  $cloudflared tunnel create $Name"
    Write-Host "  $cloudflared tunnel route dns $Name $Hostname"
    Write-Host ''
    Write-Host 'Then set these in backend/.env and restart the backend:' -ForegroundColor Yellow
    Write-Host "  GOOGLE_REDIRECT_URI=https://$Hostname/v1/integrations/google/callback" -ForegroundColor Cyan
    Write-Host ''
    Write-Host 'And add that exact URI to the OAuth client in Google Cloud Console' -ForegroundColor Yellow
    Write-Host '(APIs & Services -> Credentials -> your OAuth client -> Authorised redirect URIs).' -ForegroundColor Yellow
    Write-Host ''

    & $cloudflared tunnel run --url "http://localhost:$Port" $Name
    exit $LASTEXITCODE
}

Write-Host "Quick tunnel -> localhost:$Port" -ForegroundColor Green
Write-Host ''
Write-Host 'Watch for the trycloudflare.com URL below, then:' -ForegroundColor Yellow
Write-Host '  1. Enter it in the app under Settings -> Server address.' -ForegroundColor Yellow
Write-Host '  2. For Google: put <that URL>/v1/integrations/google/callback into' -ForegroundColor Yellow
Write-Host '     GOOGLE_REDIRECT_URI in backend/.env, add the same URI to your' -ForegroundColor Yellow
Write-Host '     Google OAuth client, and restart the backend.' -ForegroundColor Yellow
Write-Host ''
Write-Host 'This URL changes every restart. Use -Name for one that does not.' -ForegroundColor DarkGray
Write-Host ''

& $cloudflared tunnel --url "http://localhost:$Port"
