<#
.SYNOPSIS
  Stable baseline verification script (read-only, does not modify files)
.DESCRIPTION
  Verifies the working tree meets stable-board-baseline-2026-07-30 criteria:
  1. Git working tree clean (no MM/AM/??/ M/ D)
  2. Frontend JS syntax check passes
  3. Repository hygiene tests pass
  4. Full pytest suite 0 failed
.EXAMPLE
  .\scripts\verify_baseline.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pass = 0
$fail = 0
$results = @()

function Add-Result($name, $ok, $detail) {
    $script:results += [PSCustomObject]@{
        Check = $name
        Status = if ($ok) { "PASS" } else { "FAIL" }
        Detail = $detail
    }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}

Write-Host "=== Stable Baseline Verification ===" -ForegroundColor Cyan
Write-Host "Commit: $(git rev-parse --short HEAD)"
Write-Host ""

# ---- 1. Git working tree clean ----
Write-Host "[1/4] Checking git status..." -ForegroundColor Yellow
$statusLines = git status --short
$badCount = 0
if ($statusLines) {
    foreach ($line in $statusLines) {
        $prefix = $line.Substring(0, 2)
        if ($prefix -eq "MM" -or $prefix -eq "AM" -or $prefix -eq "??" -or $prefix -eq " M" -or $prefix -eq " D") {
            $badCount++
        }
    }
}
Add-Result "Git working tree clean" ($badCount -eq 0) "$badCount issues"

# ---- 2. Frontend JS syntax (auto-parsed from index.html) ----
Write-Host "[2/4] Checking JS syntax..." -ForegroundColor Yellow
$indexHtml = Get-Content -Path (Join-Path $root "static\index.html") -Raw -Encoding UTF8
$jsFiles = @()
$scriptMatches = [regex]::Matches($indexHtml, '<script\s+[^>]*src="(/static/js/[^"]+)"')
foreach ($m in $scriptMatches) {
    $src = $m.Groups[1].Value
    # Strip query string (e.g. ?v=20260729)
    $cleanPath = $src -replace '\?.*$',''
    # Convert /static/js/foo.js → static/js/foo.js
    $relPath = $cleanPath.TrimStart('/')
    if ($jsFiles -notcontains $relPath) {
        $jsFiles += $relPath
    }
}
$jsFail = @()
$jsMissing = @()
foreach ($f in $jsFiles) {
    $fullPath = Join-Path $root ($f -replace '/','\')
    if (-not (Test-Path $fullPath)) {
        $jsMissing += $f
        continue
    }
    $proc = Start-Process -FilePath "node" -ArgumentList "--check", $f -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$env:TEMP\vb_out.txt" -RedirectStandardError "$env:TEMP\vb_err.txt"
    if ($proc.ExitCode -ne 0) {
        $jsFail += $f
    }
}
$jsLabel = "JS syntax (" + $jsFiles.Count + " files)"
$jsDetailParts = @()
if ($jsFail.Count -eq 0) { $jsDetailParts += "All OK" } else { $jsDetailParts += ("Syntax failed: " + ($jsFail -join ", ")) }
if ($jsMissing.Count -gt 0) { $jsDetailParts += ("Missing: " + ($jsMissing -join ", ")) }
$jsDetail = $jsDetailParts -join "; "
Add-Result $jsLabel ($jsFail.Count -eq 0 -and $jsMissing.Count -eq 0) $jsDetail

# ---- 3. Repository hygiene tests ----
Write-Host "[3/4] Running repository hygiene tests..." -ForegroundColor Yellow
$hygRaw = cmd /c "py -m pytest tests/test_repository_hygiene.py 2>&1"
$hygOutput = $hygRaw -join "`n"
$hygPass = $hygOutput.Contains("passed") -and -not $hygOutput.Contains("failed")
$hygMatch = [regex]::Match($hygOutput, "(\d+) passed")
$hygCount = if ($hygMatch.Success) { $hygMatch.Groups[1].Value } else { "?" }
Add-Result "Repository hygiene" $hygPass ($hygCount + " passed")

# ---- 4. Full test suite ----
Write-Host "[4/4] Running full test suite..." -ForegroundColor Yellow
$testRaw = cmd /c "py -m pytest tests/ --tb=no 2>&1"
$testOutput = $testRaw -join "`n"
$testPass = $testOutput.Contains("passed") -and -not $testOutput.Contains("failed")
$testMatch = [regex]::Match($testOutput, "(\d+) passed")
$testCount = if ($testMatch.Success) { $testMatch.Groups[1].Value } else { "?" }
Add-Result "Full test suite" $testPass ($testCount + " passed")

# ---- Summary ----
Write-Host ""
Write-Host "=== Results ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize

Write-Host ""
if ($fail -eq 0) {
    Write-Host ("ALL PASS (" + $pass + "/" + $pass + ") - Baseline stable") -ForegroundColor Green
    exit 0
} else {
    Write-Host ("FAILURES (" + $fail + ") - Baseline NOT stable") -ForegroundColor Red
    exit 1
}
