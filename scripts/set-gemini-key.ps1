# Saves your Gemini API key into this project's .env file without displaying it.
# Run it yourself in a normal PowerShell window (not inside Claude):
#   powershell -ExecutionPolicy Bypass -File .\scripts\set-gemini-key.ps1
#
# Note: .env holds the key as PLAIN TEXT. It is ignored by Git and by Docker
# builds, but it is not encrypted or hidden - any program running as your
# Windows user can read it.

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot '.env'

Write-Host 'This saves GEMINI_API_KEY to:' $envFile
Write-Host 'The file is plain text (not encrypted). It is ignored by Git and Docker builds.'
$secure = Read-Host -AsSecureString 'Paste your Gemini API key (input is hidden), then press Enter'

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if (-not $key) {
    Write-Host 'No key entered - nothing was saved.'
    exit 1
}

# Keep any other lines already in .env; replace only the GEMINI_API_KEY line.
$lines = @()
if (Test-Path $envFile) {
    $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch '^\s*GEMINI_API_KEY\s*=' })
}
$lines += "GEMINI_API_KEY=$key"
[System.IO.File]::WriteAllLines($envFile, [string[]]$lines)  # UTF-8, no BOM
Remove-Variable key, lines

Write-Host 'Saved. The key was not displayed.'
Write-Host 'Restart the app so it picks up the key:  docker compose up -d --force-recreate'
