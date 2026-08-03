<#
.SYNOPSIS
  Export a consistent, read-only board-app data snapshot.
.DESCRIPTION
  SQLite files are copied through sqlite3's backup API.  The source project is
  never checkpointed, vacuumed, or otherwise opened for writing.  Ordinary
  files and vault directories retain their project-relative paths in the
  snapshot and a manifest records SHA256/size metadata for every item.
.PARAMETER DestDir
  Target directory for the snapshot.  It must be new or empty.  A directory
  under the checkout is allowed except inside the recursively copied vault.
.PARAMETER ProjectRoot
  Project root (defaults to the parent of this scripts directory).  This is
  useful for testing or for a relocated checkout; DestDir remains compatible
  with the original CLI.
.EXAMPLE
  .\scripts\export_data_snapshot.ps1 -DestDir "D:\backups\board-app\snapshot-20260804"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$DestDir,

    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$helper = Join-Path $PSScriptRoot "data_snapshot.py"

if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    throw "Snapshot helper not found: $helper"
}

# Prefer the repository virtualenv, then the standard Windows launchers.  An
# argument array keeps paths with spaces intact and avoids shell evaluation.
$python = $null
$venvPython = Join-Path $root "venv\Scripts\python.exe"
$scriptVenvPython = Join-Path (Split-Path -Parent $PSScriptRoot) "venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = $venvPython
} elseif (Test-Path -LiteralPath $scriptVenvPython -PathType Leaf) {
    $python = $scriptVenvPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
} else {
    throw "Python executable not found (expected venv\Scripts\python.exe, py, or python)."
}

Write-Host "=== Data Snapshot Export ===" -ForegroundColor Cyan
Write-Host "Source:      $root"
Write-Host "Destination: $DestDir"
Write-Host "Mode:        SQLite backup API (source read-only)" -ForegroundColor Yellow
Write-Host ""

$arguments = @(
    $helper,
    "export",
    "--project-root", $root,
    "--dest", $DestDir
)
& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "Snapshot export failed (exit code $exitCode)."
}

Write-Host "Snapshot export completed." -ForegroundColor Green
