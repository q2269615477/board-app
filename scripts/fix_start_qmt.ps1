#Requires -Version 5.1
# ASCII-only script. Discovers QMT install dir under D:\ (avoids mojibake path literals).
param(
  [switch]$SkipStart,
  [switch]$NoRebootPrompt
)

$ErrorActionPreference = 'Continue'

function Write-Step([string]$msg) {
  Write-Host "[QMT-FIX] $msg" -ForegroundColor Cyan
}

function Find-QmtRoot {
  $candidates = @()
  Get-ChildItem -LiteralPath 'D:\' -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $bin = Join-Path $_.FullName 'bin.x64\XtItClient.exe'
    if (Test-Path -LiteralPath $bin) {
      $candidates += $_.FullName
    }
  }
  if ($candidates.Count -eq 0) { return $null }
  # Prefer Datong/QMT-like folders if multiple
  $prefer = $candidates | Where-Object { $_ -match 'QMT|Datong|dtsbc' } | Select-Object -First 1
  if ($prefer) { return $prefer }
  return $candidates[0]
}

$Root = Find-QmtRoot
if (-not $Root) {
  Write-Error 'XtItClient.exe not found under D:\*\bin.x64\'
  exit 1
}
$Bin = Join-Path $Root 'bin.x64'
$Exe = Join-Path $Bin 'XtItClient.exe'
Write-Step "Root=$Root"

$acpKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage'
$acp = (Get-ItemProperty $acpKey).ACP
$oem = (Get-ItemProperty $acpKey).OEMCP
Write-Step "ACP=$acp OEMCP=$oem"

if ($acp -eq '65001') {
  try {
    Set-ItemProperty -Path $acpKey -Name 'ACP' -Value '936' -Type String
    Set-ItemProperty -Path $acpKey -Name 'OEMCP' -Value '936' -Type String
    Write-Step 'Set ACP/OEMCP to 936 (GBK). Reboot required.'
    if (-not $NoRebootPrompt) {
      $ans = Read-Host 'Reboot now? (Y/N)'
      if ($ans -match '^[Yy]') {
        shutdown /r /t 15 /c 'QMT fix: apply system codepage GBK(936)'
        exit 0
      }
    }
    Write-Step 'Please reboot, then run this script again.'
    if ($SkipStart) { exit 0 }
  } catch {
    Write-Error "Failed to change codepage (need Admin): $($_.Exception.Message)"
    exit 2
  }
}

Write-Step 'Stopping leftover QMT processes...'
Get-CimInstance Win32_Process | Where-Object {
  $_.ExecutablePath -and (
    $_.ExecutablePath.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)
  ) -and (
    $_.Name -match 'XtItClient|XtMiniQmt|miniquote|XtModel'
  )
} | ForEach-Object {
  Write-Step "Kill PID=$($_.ProcessId) $($_.Name)"
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

Write-Step 'Cleaning lock/tmp/shm leftovers...'
@(
  (Join-Path $Bin 'daemonFile.lock'),
  (Join-Path $Bin 'mini_qmt_exited.lock'),
  (Join-Path $Root 'userdata\users\xtquoterconfig.xml.tmp')
) | ForEach-Object {
  if (Test-Path -LiteralPath $_) {
    Remove-Item -LiteralPath $_ -Force -ErrorAction SilentlyContinue
    Write-Step "Removed $_"
  }
}

$mini = Join-Path $Root 'userdata_mini'
if (Test-Path -LiteralPath $mini) {
  Get-ChildItem -LiteralPath $mini -Force -ErrorAction SilentlyContinue |
    Where-Object { -not $_.PSIsContainer -and $_.Name -like 'miniqmtShm*' } |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

if ($SkipStart) {
  Write-Step 'SkipStart: not launching client'
  exit 0
}

$acpNow = (Get-ItemProperty $acpKey).ACP
if ($acpNow -eq '65001') {
  Write-Warning 'ACP still 65001 in registry. Reboot first.'
  exit 3
}

Write-Step "Launch $Exe (cwd=$Bin)"
Start-Process -FilePath $Exe -WorkingDirectory $Bin
Start-Sleep -Seconds 8

$alive = @(Get-Process -Name 'XtItClient' -ErrorAction SilentlyContinue)
if ($alive.Count -gt 0) {
  Write-Step ("XtItClient running PID=" + ($alive.Id -join ','))
} else {
  Write-Warning 'XtItClient not running. Check userdata\log\XtClient_*.log'
}

$tnc = Test-NetConnection -ComputerName 127.0.0.1 -Port 58600 -WarningAction SilentlyContinue
Write-Step ("port 58600 open=" + [bool]$tnc.TcpTestSucceeded)
Write-Step 'Login in the client UI; formula RPC 58600 is ready after login.'
