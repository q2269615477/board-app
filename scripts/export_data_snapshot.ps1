<#
.SYNOPSIS
  Export a read-only data snapshot to a user-specified directory.
.DESCRIPTION
  Copies critical data assets (SQLite databases, signals, vault) to a backup
  directory. Does NOT delete or modify original data. Does NOT commit anything
  to Git. Prints file paths and sizes before copying.
.PARAMETER DestDir
  Target directory for the snapshot. Required.
.EXAMPLE
  .\scripts\export_data_snapshot.ps1 -DestDir "D:\backups\board-app\snapshot-20260731"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$DestDir
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path $DestDir)) {
    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
}

# Files to back up (relative to project root)
$files = @(
    "data/kline.db",
    "data/stock_data.db",
    "data/annotation_index.sqlite",
    "data/session_index.sqlite",
    "data/signals.json"
)

# Directories to back up
$dirs = @(
    "vault/TradingVault"
)

Write-Host "=== Data Snapshot Export ===" -ForegroundColor Cyan
Write-Host "Source: $root"
Write-Host "Destination: $DestDir"
Write-Host ""

# Step 1: SQLite checkpoint (flush WAL to main db)
Write-Host "[1/3] Running SQLite checkpoint..." -ForegroundColor Yellow
foreach ($dbFile in @("data/kline.db", "data/stock_data.db", "data/annotation_index.sqlite", "data/session_index.sqlite")) {
    $dbPath = Join-Path $root ($dbFile -replace '/', '\')
    if (Test-Path $dbPath) {
        Write-Host "  Checkpoint: $dbFile"
        try {
            py -c "import sqlite3; c=sqlite3.connect(r'$dbPath'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
        } catch {
            Write-Host "  WARNING: checkpoint failed for $dbFile (may not have WAL)" -ForegroundColor DarkYellow
        }
    }
}
Write-Host ""

# Step 2: Copy files
Write-Host "[2/3] Copying files..." -ForegroundColor Yellow
$copiedCount = 0
foreach ($f in $files) {
    $srcPath = Join-Path $root ($f -replace '/', '\')
    if (-not (Test-Path $srcPath)) {
        Write-Host "  SKIP (not found): $f" -ForegroundColor DarkYellow
        continue
    }
    $sizeBytes = (Get-Item $srcPath).Length
    $sizeMB = [math]::Round($sizeBytes / 1MB, 1)
    Write-Host "  $f ($sizeMB MB)"

    $destFile = Join-Path $DestDir (Split-Path $f -Leaf)
    Copy-Item $srcPath $destFile -Force
    $copiedCount++
}
Write-Host ""

# Step 3: Copy directories
Write-Host "[3/3] Copying directories..." -ForegroundColor Yellow
foreach ($d in $dirs) {
    $srcPath = Join-Path $root ($d -replace '/', '\')
    if (-not (Test-Path $srcPath)) {
        Write-Host "  SKIP (not found): $d" -ForegroundColor DarkYellow
        continue
    }
    $dirSize = (Get-ChildItem $srcPath -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $dirMB = [math]::Round($dirSize / 1MB, 1)
    Write-Host "  $d ($dirMB MB)"

    $destDirName = Split-Path $d -Leaf
    $destPath = Join-Path $DestDir $destDirName
    Copy-Item -Recurse $srcPath $destPath -Force
    $copiedCount++
}
Write-Host ""

# Record backup info
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$infoFile = Join-Path $DestDir "BACKUP_INFO.txt"
@"
Backup completed: $timestamp
Source: $root
Files copied: $copiedCount
"@ | Out-File -FilePath $infoFile -Encoding UTF8

Write-Host "Snapshot exported to: $DestDir" -ForegroundColor Green
Write-Host "Files copied: $copiedCount"
Write-Host ""
Write-Host "NOTE: This snapshot is NOT committed to Git." -ForegroundColor DarkYellow
Write-Host "      Store it in a safe external location." -ForegroundColor DarkYellow
