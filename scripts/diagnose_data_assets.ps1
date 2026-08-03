<#
.SYNOPSIS
  Read-only health check for local board-app data assets.

.DESCRIPTION
  Reports SQLite file metadata, journal sidecars, a small schema summary,
  the derived search index, and the newest backup entry.  The command is
  intentionally observational: it never opens a database for writing and
  never changes files.  Use -Json for automation.  Exit codes are 0 (OK),
  1 (warnings), and 2 (critical findings or an unexpected diagnostic error).

.EXAMPLE
  .\scripts\diagnose_data_assets.ps1

.EXAMPLE
  .\scripts\diagnose_data_assets.ps1 -Json -Deep -BackupRoot D:\backups\board-app
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$DataDir = "data",
    [string]$SearchIndexPath = "static/search_index.json",
    [string]$ClassificationPath = "static/board_classification.json",
    [string]$BackupRoot = "data/backup",
    [switch]$Deep,
    [switch]$Json,
    [ValidateRange(1, 2147483647)]
    [int]$WarnWalMB = 128,
    [ValidateRange(0.0, 100000.0)]
    [double]$WarnWalRatio = 0.25,
    [ValidateRange(0, 2147483647)]
    [int]$WarnBackupAgeDays = 7,
    [ValidateRange(0, 2147483647)]
    [int]$WarnIndexAgeDays = 7,
    [ValidateRange(1, 3600)]
    [int]$SqliteTimeoutSec = 5
)

$ErrorActionPreference = "Stop"

function Convert-ToAbsolutePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$BasePath
    )

    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Add-Finding {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][ValidateSet("OK", "WARN", "CRITICAL", "SKIP")][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message,
        [hashtable]$Details = @{}
    )

    $script:Findings.Add([ordered]@{
            code     = $Code
            status   = $Status
            severity = $Status
            message  = $Message
            details  = [ordered]@{} + $Details
        }) | Out-Null
}

function Get-FileReport {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [ordered]@{
            path          = $Path
            exists        = $false
            size_bytes    = $null
            size_mb       = $null
            modified_at   = $null
            last_write_utc = $null
        }
    }

    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        path           = $Path
        exists         = $true
        size_bytes     = [int64]$item.Length
        size_mb        = [math]::Round(([double]$item.Length / 1MB), 3)
        modified_at    = $item.LastWriteTime.ToString("o")
        last_write_utc = $item.LastWriteTimeUtc.ToString("o")
    }
}

function Get-PythonExecutable {
    param([Parameter(Mandatory = $true)][string]$Root)

    $venvPython = Join-Path $Root "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return $pythonCommand.Source
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $pyCommand) {
        return $pyCommand.Source
    }
    return $null
}

function Invoke-SqliteReadOnlySummary {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    # The helper is supplied on stdin so this diagnostic does not create a
    # helper file or import application modules with startup side effects.
    $pythonCode = @'
import json
import pathlib
import sqlite3
import sys

db_path = pathlib.Path(sys.argv[1]).resolve()
timeout = float(sys.argv[2])
result = {
    "ok": False,
    "path": str(db_path),
    "query_only": False,
    "journal_mode": None,
    "schema_version": None,
    "page_count": None,
    "page_size": None,
    "tables": [],
    "kline_meta": None,
    "error": None,
}

try:
    # mode=ro prevents SQLite from creating or changing the database.  The
    # query_only pragma provides a second guard at the connection level.
    # immutable=1 prevents SQLite from taking WAL read marks or touching the
    # -shm sidecar while this process inspects a live database.  Journal files
    # are reported separately by PowerShell and are deliberately not merged.
    uri = db_path.as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    try:
        conn.execute("PRAGMA query_only=ON")
        result["query_only"] = bool(conn.execute("PRAGMA query_only").fetchone()[0])
        result["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        result["schema_version"] = conn.execute("PRAGMA schema_version").fetchone()[0]
        result["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
        result["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]

        table_rows = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        table_names = {row[0] for row in table_rows}
        large_database = bool(result["page_count"] and result["page_size"] and
                              result["page_count"] * result["page_size"] > 256 * 1024 * 1024)
        large_tokens = ("kline", "stock_data")

        for table_name, table_type in table_rows:
            table = {"name": table_name, "type": table_type, "columns": [],
                     "rows": None, "row_count_skipped": False}
            try:
                columns = conn.execute("PRAGMA table_info(\"%s\")" % table_name.replace('"', '""')).fetchall()
                table["columns"] = [row[1] for row in columns]
            except sqlite3.DatabaseError:
                table["columns"] = []

            lowered = table_name.lower()
            # Never count the market-history tables: their production copies
            # are multi-gigabyte and a diagnostic must stay bounded even when
            # a small fixture is used in tests.
            skip_count = any(token in lowered for token in large_tokens)
            if large_database and any(token in lowered for token in ("ohlcv", "price")):
                skip_count = True
            if table_type == "view":
                skip_count = True
            if skip_count:
                table["row_count_skipped"] = True
            else:
                try:
                    table["rows"] = int(conn.execute(
                        "SELECT COUNT(*) FROM \"%s\"" % table_name.replace('"', '""')
                    ).fetchone()[0])
                except sqlite3.DatabaseError:
                    table["row_count_skipped"] = True
            result["tables"].append(table)

        if "kline_meta" in table_names:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(kline_meta)").fetchall()}
            meta = {"rows": None, "periods": [], "last_updated_at": None,
                    "last_date": None}
            try:
                meta["rows"] = int(conn.execute("SELECT COUNT(*) FROM kline_meta").fetchone()[0])
            except sqlite3.DatabaseError:
                pass
            if "period" in columns:
                try:
                    meta["periods"] = [row[0] for row in conn.execute(
                        "SELECT DISTINCT period FROM kline_meta WHERE period IS NOT NULL ORDER BY period"
                    ).fetchall()]
                except sqlite3.DatabaseError:
                    pass
            if "updated_at" in columns:
                try:
                    meta["last_updated_at"] = conn.execute(
                        "SELECT MAX(updated_at) FROM kline_meta"
                    ).fetchone()[0]
                except sqlite3.DatabaseError:
                    pass
            if "last_date" in columns:
                try:
                    meta["last_date"] = conn.execute(
                        "SELECT MAX(last_date) FROM kline_meta"
                    ).fetchone()[0]
                except sqlite3.DatabaseError:
                    pass
            result["kline_meta"] = meta
        result["ok"] = True
    finally:
        conn.close()
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
'@

    $oldNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $env:PYTHONDONTWRITEBYTECODE = "1"
    try {
        $raw = ($pythonCode | & $PythonExe - $Path ([string]$TimeoutSeconds) 2>&1 | Out-String).Trim()
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        # Assigning the previous value back keeps this process-local setting
        # contained without touching the filesystem.
        $env:PYTHONDONTWRITEBYTECODE = $oldNoBytecode
    }

    if ($pythonExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        return [ordered]@{ ok = $false; error = "Python SQLite probe failed (exit $pythonExitCode): $raw" }
    }
    try {
        return $raw | ConvertFrom-Json -Depth 30
    }
    catch {
        return [ordered]@{ ok = $false; error = "Python SQLite probe returned invalid JSON: $($_.Exception.Message)" }
    }
}

function Get-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json -Depth 100
    }
    catch {
        throw $_
    }
}

function Get-IndexItems {
    param($Items)

    if ($null -eq $Items) {
        return @{}
    }
    $map = [ordered]@{}
    foreach ($property in $Items.PSObject.Properties) {
        $map[[string]$property.Name] = $property.Value
    }
    return $map
}

function Get-ClassificationCodes {
    param($Classification)

    $codes = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    function Visit-ClassificationNode {
        param($Node)

        if ($null -eq $Node) { return }
        foreach ($board in @($Node.boards)) {
            if ($null -ne $board -and -not [string]::IsNullOrWhiteSpace([string]$board.code)) {
                [void]$codes.Add([string]$board.code)
            }
        }
        foreach ($child in @($Node.subcategories)) {
            Visit-ClassificationNode -Node $child
        }
    }

    foreach ($category in @($Classification.categories)) {
        Visit-ClassificationNode -Node $category
    }
    return $codes
}

function Add-SqliteAssetReport {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][bool]$Required,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    $report = Get-FileReport -Path $Path
    $baseCode = $Name.ToUpperInvariant()
    $walPath = "$Path-wal"
    $shmPath = "$Path-shm"
    $report.wal = Get-FileReport -Path $walPath
    $report.shm = Get-FileReport -Path $shmPath
    # SQLite does not persist a reliable "last checkpoint" timestamp.  Do not
    # mislabel the main file mtime as one; a future backup manifest may record it.
    $report.checkpoint_at = $null
    $report.checkpoint_status = if ($report.wal.exists) { "unknown_or_pending" } else { "unknown_not_recorded" }

    if (-not $report.exists) {
        $status = if ($Required) { "CRITICAL" } else { "WARN" }
        Add-Finding -Code "${baseCode}_MISSING" -Status $status `
            -Message "$Name is not present." -Details @{ path = $Path; required = $Required }
        return $report
    }
    if ($report.size_bytes -eq 0) {
        $status = if ($Required) { "CRITICAL" } else { "WARN" }
        Add-Finding -Code "${baseCode}_EMPTY" -Status $status `
            -Message "$Name exists but is empty." -Details @{ path = $Path; required = $Required }
    }

    if ($report.wal.exists) {
        Add-Finding -Code "${baseCode}_WAL_PRESENT" -Status "WARN" `
            -Message "$Name has a WAL sidecar; the latest checkpoint cannot be inferred read-only." `
            -Details @{ path = $walPath; size_bytes = $report.wal.size_bytes }
        $ratio = if ($report.size_bytes -gt 0) { [double]$report.wal.size_bytes / [double]$report.size_bytes } else { [double]::PositiveInfinity }
        $report.wal_ratio = [math]::Round($ratio, 6)
        if ($report.wal.size_mb -ge $WarnWalMB -or $ratio -ge $WarnWalRatio) {
            Add-Finding -Code "${baseCode}_WAL_LARGE" -Status "WARN" `
                -Message "$Name WAL is large relative to its main file." `
                -Details @{ size_mb = $report.wal.size_mb; ratio = $report.wal_ratio; warn_mb = $WarnWalMB; warn_ratio = $WarnWalRatio }
        }
    }
    else {
        $report.wal_ratio = 0
        Add-Finding -Code "${baseCode}_WAL_ABSENT" -Status "OK" `
            -Message "$Name has no WAL sidecar." -Details @{ path = $walPath }
    }
    if ($report.shm.exists) {
        Add-Finding -Code "${baseCode}_SHM_PRESENT" -Status "OK" `
            -Message "$Name has a shared-memory sidecar." -Details @{ path = $shmPath; size_bytes = $report.shm.size_bytes }
    }
    else {
        Add-Finding -Code "${baseCode}_SHM_ABSENT" -Status "OK" `
            -Message "$Name has no shared-memory sidecar." -Details @{ path = $shmPath }
    }

    if ($report.size_bytes -gt 0 -and $null -ne $PythonExe) {
        $sqlite = Invoke-SqliteReadOnlySummary -Path $Path -PythonExe $PythonExe -TimeoutSeconds $SqliteTimeoutSec
        $report.sqlite = $sqlite
        if ($sqlite.ok -and $sqlite.query_only) {
            Add-Finding -Code "${baseCode}_SQLITE_READ_ONLY" -Status "OK" `
                -Message "$Name schema was read through a read-only query-only connection." `
                -Details @{ tables = @($sqlite.tables).Count; schema_version = $sqlite.schema_version }
        }
        else {
            Add-Finding -Code "${baseCode}_SQLITE_UNREADABLE" -Status "CRITICAL" `
                -Message "$Name could not be inspected as SQLite: $($sqlite.error)" -Details @{ path = $Path }
        }
    }
    elseif ($null -eq $PythonExe) {
        Add-Finding -Code "${baseCode}_SQLITE_PROBE_SKIPPED" -Status "SKIP" `
            -Message "Python is unavailable; SQLite schema inspection was skipped." -Details @{ path = $Path }
    }
    return $report
}

function Invoke-Diagnostic {
    $script:Findings = [Collections.Generic.List[object]]::new()
    $root = Convert-ToAbsolutePath -Path $ProjectRoot -BasePath (Get-Location).Path
    $dataRoot = Convert-ToAbsolutePath -Path $DataDir -BasePath $root
    $searchPath = Convert-ToAbsolutePath -Path $SearchIndexPath -BasePath $root
    $classificationPath = Convert-ToAbsolutePath -Path $ClassificationPath -BasePath $root
    $backupPath = Convert-ToAbsolutePath -Path $BackupRoot -BasePath $root
    $pythonExe = Get-PythonExecutable -Root $root

    $assets = [ordered]@{}
    $assetSpecs = @(
        @{ Name = "KLINE_DB"; Relative = "kline.db"; Required = $true },
        @{ Name = "STOCK_DATA_DB"; Relative = "stock_data.db"; Required = $false },
        @{ Name = "ANNOTATION_INDEX"; Relative = "annotation_index.sqlite"; Required = $false },
        @{ Name = "SESSION_INDEX"; Relative = "session_index.sqlite"; Required = $false }
    )
    foreach ($spec in $assetSpecs) {
        $assetPath = Join-Path $dataRoot $spec.Relative
        $assets[$spec.Name.ToLowerInvariant()] = Add-SqliteAssetReport `
            -Name $spec.Name -Path $assetPath -Required ([bool]$spec.Required) -PythonExe $pythonExe
    }

    $searchReport = [ordered]@{
        path                         = $searchPath
        exists                       = $false
        size_bytes                   = $null
        modified_at                  = $null
        built_at                     = $null
        age_days                     = $null
        total                        = $null
        item_count                   = $null
        classification_count         = $null
        classification_overlap       = $null
        classification_coverage      = $null
        rebuild_script               = Join-Path $root "build_search_index.py"
        rebuild_available            = $false
    }
    if (Test-Path -LiteralPath $searchReport.rebuild_script -PathType Leaf) {
        $searchReport.rebuild_available = $true
        Add-Finding -Code "SEARCH_INDEX_REBUILD_AVAILABLE" -Status "OK" `
            -Message "The search index has a local rebuild script." -Details @{ path = $searchReport.rebuild_script }
    }
    else {
        Add-Finding -Code "SEARCH_INDEX_REBUILD_UNAVAILABLE" -Status "WARN" `
            -Message "The search index rebuild script is not present." -Details @{ path = $searchReport.rebuild_script }
    }

    if (-not (Test-Path -LiteralPath $searchPath -PathType Leaf)) {
        Add-Finding -Code "SEARCH_INDEX_MISSING" -Status "WARN" `
            -Message "The derived search index is missing." -Details @{ path = $searchPath; rebuild_available = $searchReport.rebuild_available }
    }
    else {
        $searchItem = Get-Item -LiteralPath $searchPath
        $searchReport.exists = $true
        $searchReport.size_bytes = [int64]$searchItem.Length
        $searchReport.modified_at = $searchItem.LastWriteTime.ToString("o")
        try {
            $searchJson = Get-JsonFile -Path $searchPath
            $items = Get-IndexItems -Items $searchJson.items
            $searchReport.item_count = $items.Count
            $searchReport.total = if ($null -eq $searchJson.total) { $null } else { [int]$searchJson.total }
            if ($null -eq $searchJson.items -or $items.Count -eq 0) {
                Add-Finding -Code "SEARCH_INDEX_SCHEMA_INVALID" -Status "CRITICAL" `
                    -Message "The search index has no items map." -Details @{ path = $searchPath }
            }
            elseif ($null -ne $searchReport.total -and $searchReport.total -ne $searchReport.item_count) {
                Add-Finding -Code "SEARCH_INDEX_TOTAL_MISMATCH" -Status "WARN" `
                    -Message "The search index total does not match its item map." `
                    -Details @{ total = $searchReport.total; item_count = $searchReport.item_count }
            }
            else {
                Add-Finding -Code "SEARCH_INDEX_SCHEMA_OK" -Status "OK" `
                    -Message "The search index JSON shape and item count are valid." `
                    -Details @{ total = $searchReport.total; item_count = $searchReport.item_count }
            }

            if ($null -ne $searchJson.built_at) {
                $parsedBuiltAt = [datetime]::MinValue
                if ([datetime]::TryParse([string]$searchJson.built_at, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeLocal, [ref]$parsedBuiltAt)) {
                    $searchReport.built_at = $parsedBuiltAt.ToString("o")
                    $searchReport.age_days = [math]::Max(0, [math]::Round(((Get-Date) - $parsedBuiltAt).TotalDays, 3))
                    if ($searchReport.age_days -gt $WarnIndexAgeDays) {
                        Add-Finding -Code "SEARCH_INDEX_STALE" -Status "WARN" `
                            -Message "The search index is older than the configured freshness window." `
                            -Details @{ age_days = $searchReport.age_days; warn_days = $WarnIndexAgeDays }
                    }
                    else {
                        Add-Finding -Code "SEARCH_INDEX_FRESH" -Status "OK" `
                            -Message "The search index built_at timestamp is within the freshness window." `
                            -Details @{ age_days = $searchReport.age_days; warn_days = $WarnIndexAgeDays }
                    }
                }
                else {
                    Add-Finding -Code "SEARCH_INDEX_BUILT_AT_INVALID" -Status "WARN" `
                        -Message "The search index built_at value is not a timestamp." -Details @{ built_at = $searchJson.built_at }
                }
            }
            else {
                Add-Finding -Code "SEARCH_INDEX_BUILT_AT_MISSING" -Status "WARN" `
                    -Message "The search index does not record built_at." -Details @{}
            }

            if (Test-Path -LiteralPath $classificationPath -PathType Leaf) {
                try {
                    $classificationJson = Get-JsonFile -Path $classificationPath
                    $classificationCodes = Get-ClassificationCodes -Classification $classificationJson
                    $indexCodes = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
                    foreach ($key in $items.Keys) { [void]$indexCodes.Add([string]$key) }
                    $overlap = 0
                    foreach ($code in $classificationCodes) { if ($indexCodes.Contains($code)) { $overlap++ } }
                    $searchReport.classification_count = $classificationCodes.Count
                    $searchReport.classification_overlap = $overlap
                    $searchReport.classification_coverage = if ($classificationCodes.Count) { [math]::Round($overlap / $classificationCodes.Count, 6) } else { $null }
                    if ($classificationCodes.Count -and $overlap -lt $classificationCodes.Count) {
                        Add-Finding -Code "SEARCH_INDEX_CLASSIFICATION_GAP" -Status "WARN" `
                            -Message "Some classified boards are absent from the search index." `
                            -Details @{ classification_count = $classificationCodes.Count; overlap = $overlap; coverage = $searchReport.classification_coverage }
                    }
                    else {
                        Add-Finding -Code "SEARCH_INDEX_CLASSIFICATION_COVERED" -Status "OK" `
                            -Message "All classified board codes are represented in the search index." `
                            -Details @{ classification_count = $classificationCodes.Count; overlap = $overlap; coverage = $searchReport.classification_coverage }
                    }
                }
                catch {
                    Add-Finding -Code "CLASSIFICATION_INVALID" -Status "WARN" `
                        -Message "The classification JSON could not be inspected: $($_.Exception.Message)" -Details @{ path = $classificationPath }
                }
            }
            else {
                Add-Finding -Code "CLASSIFICATION_MISSING" -Status "WARN" `
                    -Message "The classification file is not present; coverage was skipped." -Details @{ path = $classificationPath }
            }
        }
        catch {
            Add-Finding -Code "SEARCH_INDEX_INVALID" -Status "CRITICAL" `
                -Message "The search index is not valid JSON: $($_.Exception.Message)" -Details @{ path = $searchPath }
        }
    }

    $backupReport = [ordered]@{
        root          = $backupPath
        exists        = $false
        latest_path   = $null
        latest_at     = $null
        age_days      = $null
        manifest_path = $null
        deep_sqlite   = $null
    }
    if (-not (Test-Path -LiteralPath $backupPath -PathType Container)) {
        Add-Finding -Code "BACKUP_ROOT_MISSING" -Status "CRITICAL" `
            -Message "The configured backup root is not present." -Details @{ path = $backupPath }
    }
    else {
        $backupReport.exists = $true
        $entries = @(Get-ChildItem -LiteralPath $backupPath -Force -ErrorAction Stop)
        if ($entries.Count -eq 0) {
            Add-Finding -Code "BACKUP_EMPTY" -Status "CRITICAL" `
                -Message "The configured backup root contains no entries." -Details @{ path = $backupPath }
        }
        else {
            $latest = $entries | Sort-Object -Property LastWriteTimeUtc -Descending | Select-Object -First 1
            $backupReport.latest_path = $latest.FullName
            $backupReport.latest_at = $latest.LastWriteTimeUtc.ToString("o")
            $backupReport.age_days = [math]::Max(0, [math]::Round(((Get-Date).ToUniversalTime() - $latest.LastWriteTimeUtc).TotalDays, 3))
            if ($backupReport.age_days -gt $WarnBackupAgeDays) {
                Add-Finding -Code "BACKUP_STALE" -Status "WARN" `
                    -Message "The newest backup entry is older than the configured freshness window." `
                    -Details @{ age_days = $backupReport.age_days; warn_days = $WarnBackupAgeDays; path = $latest.FullName }
            }
            else {
                Add-Finding -Code "BACKUP_FRESH" -Status "OK" `
                    -Message "The newest backup entry is within the freshness window." `
                    -Details @{ age_days = $backupReport.age_days; warn_days = $WarnBackupAgeDays; path = $latest.FullName }
            }

            $metadataCandidates = @()
            if ($latest.PSIsContainer) {
                $metadataCandidates += Join-Path $latest.FullName "BACKUP_INFO.txt"
                $metadataCandidates += Join-Path $latest.FullName "manifest.json"
            }
            elseif ($latest.Name -in @("BACKUP_INFO.txt", "manifest.json")) {
                $metadataCandidates += $latest.FullName
            }
            foreach ($candidate in $metadataCandidates) {
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    $backupReport.manifest_path = $candidate
                    break
                }
            }
            if ($null -eq $backupReport.manifest_path) {
                Add-Finding -Code "BACKUP_MANIFEST_MISSING" -Status "WARN" `
                    -Message "The newest backup entry has no BACKUP_INFO.txt or manifest.json." `
                    -Details @{ path = $latest.FullName }
            }
            else {
                Add-Finding -Code "BACKUP_MANIFEST_PRESENT" -Status "OK" `
                    -Message "Backup metadata is present for the newest entry." -Details @{ path = $backupReport.manifest_path }
            }

            if ($Deep) {
                $backupDb = if ($latest.PSIsContainer) { Join-Path $latest.FullName "kline.db" } else { $null }
                if ($null -ne $backupDb -and (Test-Path -LiteralPath $backupDb -PathType Leaf) -and $null -ne $pythonExe) {
                    $backupReport.deep_sqlite = Invoke-SqliteReadOnlySummary -Path $backupDb -PythonExe $pythonExe -TimeoutSeconds $SqliteTimeoutSec
                    if ($backupReport.deep_sqlite.ok -and $backupReport.deep_sqlite.query_only) {
                        Add-Finding -Code "BACKUP_SQLITE_READ_ONLY" -Status "OK" `
                            -Message "The newest backup kline.db passed a read-only schema probe." -Details @{ path = $backupDb }
                    }
                    else {
                        Add-Finding -Code "BACKUP_SQLITE_UNREADABLE" -Status "CRITICAL" `
                            -Message "The newest backup kline.db failed a read-only schema probe." -Details @{ path = $backupDb; error = $backupReport.deep_sqlite.error }
                    }
                }
                else {
                    Add-Finding -Code "BACKUP_SQLITE_PROBE_SKIPPED" -Status "SKIP" `
                        -Message "No kline.db was available in the newest backup entry for deep inspection." `
                        -Details @{ path = $latest.FullName }
                }
            }
        }
    }

    $rank = @{ OK = 0; SKIP = 0; WARN = 1; CRITICAL = 2 }
    $highest = 0
    foreach ($finding in $script:Findings) {
        $value = [int]$rank[$finding.status]
        if ($value -gt $highest) { $highest = $value }
    }
    $overall = if ($highest -ge 2) { "CRITICAL" } elseif ($highest -eq 1) { "WARN" } else { "OK" }
    $summary = [ordered]@{
        status       = $overall
        exit_code    = $highest
        finding_count = $script:Findings.Count
        critical     = @($script:Findings | Where-Object status -eq "CRITICAL").Count
        warnings     = @($script:Findings | Where-Object status -eq "WARN").Count
        skipped      = @($script:Findings | Where-Object status -eq "SKIP").Count
    }

    return [ordered]@{
        schema_version = 1
        generated_at   = [datetime]::UtcNow.ToString("o")
        project_root   = $root
        data_dir       = $dataRoot
        deep           = [bool]$Deep
        summary        = $summary
        findings       = @($script:Findings)
        assets         = $assets
        search_index   = $searchReport
        backup         = $backupReport
    }
}

try {
    $result = Invoke-Diagnostic
    if ($Json) {
        $result | ConvertTo-Json -Depth 30 -Compress
    }
    else {
        Write-Output ("Data asset diagnostic: {0} (exit {1})" -f $result.summary.status, $result.summary.exit_code)
        Write-Output ("Project: {0}" -f $result.project_root)
        foreach ($finding in $result.findings) {
            Write-Output ("[{0}] {1}: {2}" -f $finding.status, $finding.code, $finding.message)
        }
    }
    exit ([int]$result.summary.exit_code)
}
catch {
    $fatal = [ordered]@{
        schema_version = 1
        generated_at   = [datetime]::UtcNow.ToString("o")
        summary        = [ordered]@{ status = "CRITICAL"; exit_code = 2; finding_count = 1; critical = 1; warnings = 0; skipped = 0 }
        findings       = @([ordered]@{ code = "DIAGNOSTIC_ERROR"; status = "CRITICAL"; severity = "CRITICAL"; message = $_.Exception.Message; details = @{} })
    }
    if ($Json) {
        $fatal | ConvertTo-Json -Depth 20 -Compress
    }
    else {
        Write-Output ("Data asset diagnostic: CRITICAL (exit 2)")
        Write-Output ("[CRITICAL] DIAGNOSTIC_ERROR: {0}" -f $_.Exception.Message)
    }
    exit 2
}
