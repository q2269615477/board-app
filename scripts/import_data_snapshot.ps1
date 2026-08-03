<#
.SYNOPSIS
  Import a data snapshot from a backup directory.
.DESCRIPTION
  Restores critical data assets from a snapshot directory. Does NOT delete
  existing data (overwrites in place). Requires explicit -Confirm parameter.
  Without -Confirm, runs in dry-run mode showing what would be restored.
.PARAMETER SrcDir
  Source directory containing the snapshot. Required.
.PARAMETER Confirm
  Must be passed to actually perform the restore. Without it, only dry-run.
.EXAMPLE
  .\scripts\import_data_snapshot.ps1 -SrcDir "D:\backups\board-app\snapshot-20260731" -Confirm
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$SrcDir,

    [switch]$Confirm
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path $SrcDir)) {
    Write-Host "ERROR: Source directory does not exist: $SrcDir" -ForegroundColor Red
    exit 1
}

# Files to restore: (source filename, destination relative path)
$files = @(
    @{Src="kline.db";                  Dest="data/kline.db"},
    @{Src="stock_data.db";             Dest="data/stock_data.db"},
    @{Src="annotation_index.sqlite";   Dest="data/annotation_index.sqlite"},
    @{Src="session_index.sqlite";      Dest="data/session_index.sqlite"},
    @{Src="signals.json";              Dest="data/signals.json"}
)

# Directories to restore
$dirs = @(
    @{Src="TradingVault"; Dest="vault/TradingVault"}
)

Write-Host "=== Data Snapshot Import ===" -ForegroundColor Cyan
Write-Host "Source:  $SrcDir"
Write-Host "Target:  $root"
if (-not $Confirm) {
    Write-Host "Mode:    DRY-RUN (pass -Confirm to execute)" -ForegroundColor DarkYellow
} else {
    Write-Host "Mode:    RESTORE" -ForegroundColor Red
}
Write-Host ""

# Show what will be restored
Write-Host "Files to restore:" -ForegroundColor Yellow
foreach ($f in $files) {
    $srcPath = Join-Path $SrcDir $f.Src
    if (Test-Path $srcPath) {
        $sizeMB = [math]::Round((Get-Item $srcPath).Length / 1MB, 1)
        $destPath = Join-Path $root ($f.Dest -replace '/', '\')
        $exists = if (Test-Path $destPath) { "(OVERWRITE)" } else { "(NEW)" }
        Write-Host "  $($f.Src) -> $($f.Dest) ($sizeMB MB) $exists"
    } else {
        Write-Host "  SKIP (not in snapshot): $($f.Src)" -ForegroundColor DarkYellow
    }
}

Write-Host ""
Write-Host "Directories to restore:" -ForegroundColor Yellow
foreach ($d in $dirs) {
    $srcPath = Join-Path $SrcDir $d.Src
    if (Test-Path $srcPath) {
        $dirSize = (Get-ChildItem $srcPath -Recurse -File | Measure-Object -Property Length -Sum).Sum
        $dirMB = [math]::Round($dirSize / 1MB, 1)
        Write-Host "  $($d.Src) -> $($d.Dest) ($dirMB MB)"
    } else {
        Write-Host "  SKIP (not in snapshot): $($d.Src)" -ForegroundColor DarkYellow
    }
}

# Dry-run stop
if (-not $Confirm) {
    Write-Host ""
    Write-Host "DRY-RUN complete. No files were modified." -ForegroundColor DarkYellow
    Write-Host "To execute: .\scripts\import_data_snapshot.ps1 -SrcDir `"$SrcDir`" -Confirm" -ForegroundColor DarkYellow
    exit 0
}

# Confirm prompt
Write-Host ""
Write-Host "WARNING: This will overwrite existing data files." -ForegroundColor Red
$response = Read-Host "Type 'RESTORE' to confirm"
if ($response -ne "RESTORE") {
    Write-Host "Aborted. No files were modified." -ForegroundColor DarkYellow
    exit 0
}

# Execute restore
Write-Host ""
Write-Host "Restoring files..." -ForegroundColor Yellow
$restoredCount = 0
foreach ($f in $files) {
    $srcPath = Join-Path $SrcDir $f.Src
    if (-not (Test-Path $srcPath)) {
        continue
    }
    $destPath = Join-Path $root ($f.Dest -replace '/', '\')
    $destDir = Split-Path $destPath -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    }
    Copy-Item $srcPath $destPath -Force
    $restoredCount++
    Write-Host "  Restored: $($f.Dest)"
}

Write-Host ""
Write-Host "Restoring directories..." -ForegroundColor Yellow
foreach ($d in $dirs) {
    $srcPath = Join-Path $SrcDir $d.Src
    if (-not (Test-Path $srcPath)) {
        continue
    }
    $destPath = Join-Path $root ($d.Dest -replace '/', '\')
    $destParent = Split-Path $destPath -Parent
    if (-not (Test-Path $destParent)) {
        New-Item -ItemType Directory -Force -Path $destParent | Out-Null
    }
    Copy-Item -Recurse $srcPath $destPath -Force
    $restoredCount++
    Write-Host "  Restored: $($d.Dest)"
}

Write-Host ""
Write-Host "Restore complete. $restoredCount items restored." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Rebuild search index:  python build_search_index.py"
Write-Host "  2. Run baseline verify:   .\scripts\verify_baseline.ps1"
Write-Host "  3. Start the app:         python app.py"
