<#
.SYNOPSIS
  Validate or restore a board-app data snapshot.
.DESCRIPTION
  Without -Confirm this command is a read-only dry-run: it validates the
  manifest, SHA256 values, and SQLite quick_check and prints safety risks.
  A real restore requires both -Confirm and an explicit non-interactive token
  (-ConfirmValue RESTORE for merge, RESTORE_EXACT for exact vault replacement),
  plus -Stopped to declare that Flask/QMT writers have been stopped.

  The default vault mode is Merge.  It never deletes files that are not in the
  snapshot.  Exact mode is explicit and first creates a same-volume recovery
  copy before replacing the vault tree.
.PARAMETER SrcDir
  Source directory containing a manifest.json snapshot.  Required.
.PARAMETER Confirm
  Enables an actual restore; without it the command is always dry-run.
.PARAMETER ConfirmValue
  Non-interactive confirmation token.  RESTORE is required for Merge and
  RESTORE_EXACT is required for Exact.  The old -Confirm switch is retained,
  but an interactive prompt is intentionally not used.
.PARAMETER Stopped
  Explicitly declares that application/QMT writers are stopped.  Required for
  an actual restore; existing SQLite WAL/SHM sidecars are moved into the
  recovery copy instead of being mixed with the restored database.
.PARAMETER VaultMode
  Merge (default) preserves existing vault files; Exact replaces the vault
  tree after creating a protection copy.
.PARAMETER ProjectRoot
  Project root (defaults to the parent of this scripts directory).
.EXAMPLE
  .\scripts\import_data_snapshot.ps1 -SrcDir "D:\backups\snapshot"
.EXAMPLE
  .\scripts\import_data_snapshot.ps1 -SrcDir "D:\backups\snapshot" -Confirm -ConfirmValue RESTORE -Stopped
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$SrcDir,

    [switch]$Confirm,

    [string]$ConfirmValue,

    [switch]$Stopped,

    [ValidateSet("Merge", "Exact")]
    [string]$VaultMode = "Merge",

    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$helper = Join-Path $PSScriptRoot "data_snapshot.py"

if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    throw "Snapshot helper not found: $helper"
}

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

$modeLower = $VaultMode.ToLowerInvariant()
$arguments = @(
    $helper,
    "restore",
    "--project-root", $root,
    "--src", $SrcDir,
    "--vault-mode", $modeLower,
    "--dry-run"
)

if ($Confirm) {
    if ([string]::IsNullOrWhiteSpace($ConfirmValue)) {
        throw "A non-interactive confirmation token is required. Use -ConfirmValue RESTORE (or RESTORE_EXACT for -VaultMode Exact)."
    }
    # Replace the dry-run argument only after -Confirm and the explicit token
    # have both been supplied.  This keeps accidental invocations harmless.
    $arguments = @(
        $helper,
        "restore",
        "--project-root", $root,
        "--src", $SrcDir,
        "--vault-mode", $modeLower,
        "--confirm-value", $ConfirmValue
    )
    if ($Stopped) {
        $arguments += "--stopped"
    }
}

Write-Host "=== Data Snapshot Import ===" -ForegroundColor Cyan
Write-Host "Source:  $SrcDir"
Write-Host "Target:  $root"
Write-Host "Vault:   $VaultMode"
if ($Confirm) {
    Write-Host "Mode:    RESTORE (explicit confirmation)" -ForegroundColor Red
} else {
    Write-Host "Mode:    DRY-RUN (no files modified)" -ForegroundColor Yellow
}
Write-Host ""

& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "Snapshot import validation/restore failed (exit code $exitCode)."
}

if ($Confirm) {
    Write-Host "Snapshot restore completed; inspect the reported protection copy before restarting the app." -ForegroundColor Green
} else {
    Write-Host "Dry-run completed. No files were modified." -ForegroundColor Yellow
}
