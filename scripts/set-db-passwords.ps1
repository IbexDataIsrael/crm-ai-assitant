# Sets the database passwords of the app role (crm_app) and the test role (crm_test).
# Run it yourself in a normal PowerShell window (not inside Claude), with the
# containers running (docker compose up -d):
#   powershell -ExecutionPolicy Bypass -File .\scripts\set-db-passwords.ps1
#
# For each role, type a password (input is hidden) or just press Enter to
# generate a random one (recommended). The script sets the passwords in
# PostgreSQL first, then saves APP_DB_PASSWORD and TEST_DB_PASSWORD to .env.
# Nothing is displayed.
#
# Note: .env holds the passwords as PLAIN TEXT. It is ignored by Git and by
# Docker builds, but it is not encrypted - any program running as your
# Windows user can read it.

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot '.env'
$sqlFile = Join-Path $PSScriptRoot 'db\set-passwords.sql'

function Read-Secret([string]$prompt) {
    $secure = Read-Host -AsSecureString $prompt
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function New-RandomPassword {
    # 32 characters from a 64-character alphabet: 192 random bits, no modulo bias,
    # and nothing that needs quoting anywhere.
    $alphabet = [char[]]'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return -join ($bytes | ForEach-Object { $alphabet[$_ % 64] })
}

function Get-Password([string]$role) {
    while ($true) {
        $first = Read-Secret "Password for $role (hidden; press Enter to generate a random one)"
        if (-not $first) {
            Write-Host "Generated a random 32-character password for $role (not displayed)."
            return New-RandomPassword
        }
        if ($first.Length -lt 12) {
            Write-Host 'Too short: use at least 12 characters. Try again.'
            continue
        }
        # .env stores the value in single quotes, so Docker Compose takes every
        # other character literally ($, #, spaces, ", \, `). A single quote or a
        # line break cannot be stored that way.
        if ($first.Contains("'") -or $first.Contains("`r") -or $first.Contains("`n")) {
            Write-Host "A password cannot contain a single quote (') or a line break. Try again."
            continue
        }
        $second = Read-Secret "Type the password for $role again"
        if ($first -cne $second) {
            Write-Host 'The two entries differ. Try again.'
            continue
        }
        return $first
    }
}

Push-Location $projectRoot
try {
    $running = @(docker compose ps --status running --services)
    if ($running -notcontains 'db') {
        Write-Host 'The db container is not running. Start it with: docker compose up -d'
        exit 1
    }

    $appPassword = Get-Password 'crm_app (the app)'
    $testPassword = Get-Password 'crm_test (the tests)'

    # 1. PostgreSQL. The passwords go only into this one docker process's
    #    environment (-e NAME without a value copies it from here), never onto a
    #    command line. The admin logs in through the container's local socket.
    $env:APP_DB_PASSWORD = $appPassword
    $env:TEST_DB_PASSWORD = $testPassword
    try {
        Get-Content -Raw $sqlFile |
            docker compose exec -T -e APP_DB_PASSWORD -e TEST_DB_PASSWORD db psql -U crm_assistant -d crm_assistant -v ON_ERROR_STOP=1 -q
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Setting the passwords in PostgreSQL failed - .env was not changed.'
            exit 1
        }
    } finally {
        Remove-Item Env:APP_DB_PASSWORD, Env:TEST_DB_PASSWORD -ErrorAction SilentlyContinue
    }

    # 2. .env. Keep every other line; replace only the two password lines.
    $lines = @()
    if (Test-Path $envFile) {
        $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch '^\s*(APP|TEST)_DB_PASSWORD\s*=' })
    }
    $lines += "APP_DB_PASSWORD='$appPassword'"
    $lines += "TEST_DB_PASSWORD='$testPassword'"
    [System.IO.File]::WriteAllLines($envFile, [string[]]$lines)  # UTF-8, no BOM
    Remove-Variable appPassword, testPassword, lines

    Write-Host 'Saved to PostgreSQL and .env. The passwords were not displayed.'
    Write-Host 'Restart the app so it picks them up:  docker compose up -d --force-recreate app'
} finally {
    Pop-Location
}
