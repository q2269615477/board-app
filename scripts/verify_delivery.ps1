# verify_delivery.ps1 - One-click delivery verification entry point
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\verify_delivery.ps1
#
# Steps:
#   1) strict validate   - board classification v5.0 strict validation
#   2) build_search_index - build full-market search index
#   3) node --check      - frontend core JS syntax check (nav/search/sse/toast/session)
#   4) frontend_smoke_test.js - frontend smoke test (optional, runs if exists)
#   5) report_tag_quality.py  - tag quality report (optional, runs if exists)
#   6) pytest tests -q --tb=short - full unit test suite
#
# Any step fails = immediate stop at first failure.

$ErrorActionPreference = 'Stop'
$PsDefaultParameterValues['*:ErrorAction'] = 'Stop'

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ROOT

function Write-StepHeader($name) {
    $line = '=' * 60
    Write-Host '' 
    Write-Host $line -ForegroundColor Cyan
    Write-Host "[BEGIN] $name" -ForegroundColor Cyan
    Write-Host $line -ForegroundColor Cyan
}

function Invoke-Step($name, $scriptBlock) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-StepHeader $name
    try {
        & $scriptBlock
        if ($LASTEXITCODE -ne 0) {
            throw "exit code = $LASTEXITCODE"
        }
        $sw.Stop()
        $elapsed = $sw.Elapsed.ToString('mm\:ss\.fff')
        Write-Host "[PASS] $name  ($elapsed)" -ForegroundColor Green
    }
    catch {
        $sw.Stop()
        $elapsed = $sw.Elapsed.ToString('mm\:ss\.fff')
        Write-Host "[FAIL] $name  ($elapsed)" -ForegroundColor Red
        Write-Host "  $_" -ForegroundColor Red
        exit 1
    }
}

function Write-Skip($message) {
    Write-Host ''
    Write-Host "[skip] $message" -ForegroundColor Yellow
}

function Write-Info($message) {
    Write-Host "[env] $message" -ForegroundColor DarkGray
}

function Write-Err($message) {
    Write-Host "[ERROR] $message" -ForegroundColor Red
}

# ---------- Locate Python ----------
$PYTHON = $null
$venvPy = Join-Path $ROOT 'venv\Scripts\python.exe'
if (Test-Path $venvPy) {
    $PYTHON = $venvPy
} else {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $PYTHON = $pyCmd.Source
    } else {
        Write-Err 'python not found. Create venv or add python to PATH.'
        exit 1
    }
}
Write-Info "PYTHON = $PYTHON"

# ---------- Locate Node ----------
$NODE = $null
$nodeCandidates = @()

$nodeInPath = Get-Command node -ErrorAction SilentlyContinue
if ($nodeInPath) {
    $nodeCandidates += $nodeInPath.Source
}

$nodeCandidates += 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$nodeCandidates += "$env:APPDATA\ms-playwright\node\bin\node.exe"
$nodeCandidates += "$env:LOCALAPPDATA\ms-playwright\node\bin\node.exe"
$nodeCandidates += "$env:APPDATA\npm\node_modules\playwright\node.exe"
$nodeCandidates += 'C:\Program Files\nodejs\node.exe'
$nodeCandidates += 'C:\Program Files (x86)\nodejs\node.exe'

foreach ($c in $nodeCandidates) {
    if (Test-Path $c) {
        $NODE = $c
        break
    }
}
if (-not $NODE) {
    Write-Err 'node not found. Tried:'
    foreach ($c in $nodeCandidates) {
        Write-Host "  $c" -ForegroundColor Red
    }
    exit 1
}
Write-Info "NODE   = $NODE"

# ---------- Frontend core JS list ----------
$FRONTEND_JS = @(
    'static\js\nav-panel.js'
    'static\js\search-panel.js'
    'static\js\sse-client.js'
    'static\js\toast-modal.js'
    'static\js\session-ui.js'
)

# ---------- Step 1: strict validate ----------
Invoke-Step 'strict validate' {
    & $PYTHON 'scripts\validate_board_classification.py'
}

# ---------- Step 2: build_search_index ----------
Invoke-Step 'build_search_index' {
    & $PYTHON 'build_search_index.py'
}

# ---------- Step 3: node --check core JS ----------
Invoke-Step 'node --check (nav/search/sse/toast/session)' {
    foreach ($rel in $FRONTEND_JS) {
        $full = Join-Path $ROOT $rel
        if (-not (Test-Path $full)) {
            Write-Host "  [skip] $rel (not found)" -ForegroundColor Yellow
            continue
        }
        & $NODE --check $full
        if ($LASTEXITCODE -ne 0) {
            throw "node --check failed: $rel (exit $LASTEXITCODE)"
        }
    }
}

# ---------- Step 4: frontend smoke test (optional) ----------
$smokeTest = Join-Path $ROOT 'scripts\frontend_smoke_test.js'
if (Test-Path $smokeTest) {
    Invoke-Step 'frontend_smoke_test.js' {
        & $NODE $smokeTest
    }
}
else {
    Write-Skip 'scripts\frontend_smoke_test.js not found, skipped'
}

# ---------- Step 5: tag quality report (optional) ----------
$tagReport = Join-Path $ROOT 'scripts\report_tag_quality.py'
if (Test-Path $tagReport) {
    Invoke-Step 'report_tag_quality.py' {
        & $PYTHON $tagReport
    }
}
else {
    Write-Skip 'scripts\report_tag_quality.py not found, skipped'
}

# ---------- Step 6: pytest full test suite ----------
Invoke-Step 'pytest tests -q --tb=short' {
    & $PYTHON -m pytest tests -q --tb=short
}

# ---------- Done ----------
$doneLine = '=' * 60
Write-Host ''
Write-Host $doneLine -ForegroundColor Green
Write-Host '[DONE] All delivery verification steps passed' -ForegroundColor Green
Write-Host $doneLine -ForegroundColor Green