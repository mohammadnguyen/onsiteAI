# scripts/gen-types.ps1
#
# Regenerate shared TypeScript types for the mobile + admin apps from the
# running backend's OpenAPI spec. This is the canonical script on Windows.
#
# Usage:
#   pwsh scripts/gen-types.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/gen-types.ps1
#
# Prereqs: backend running on http://localhost:8000 and Node.js on PATH
# (npx is used to fetch openapi-typescript on demand).

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Tmp  = Join-Path $env:TEMP 'sitetracker-openapi.json'

$MobileOut = Join-Path $Root 'mobile/src/api/types.ts'
$AdminOut  = Join-Path $Root 'admin/src/api/types.ts'

Write-Host "Fetching OpenAPI spec from http://localhost:8000/openapi.json"
Invoke-WebRequest -Uri 'http://localhost:8000/openapi.json' -OutFile $Tmp -UseBasicParsing

# Ensure output directories exist (mobile/ and admin/ may be just .gitkeep today).
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $MobileOut) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $AdminOut)  | Out-Null

Write-Host "Generating $MobileOut"
& npx -y openapi-typescript $Tmp -o $MobileOut
if ($LASTEXITCODE -ne 0) { throw "openapi-typescript failed for mobile (exit $LASTEXITCODE)" }

Write-Host "Generating $AdminOut"
& npx -y openapi-typescript $Tmp -o $AdminOut
if ($LASTEXITCODE -ne 0) { throw "openapi-typescript failed for admin (exit $LASTEXITCODE)" }

Remove-Item $Tmp -Force
Write-Host "Done."
