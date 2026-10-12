<#
.SYNOPSIS
  Import a data zip made with export_data.ps1 into THIS computer's database and recordings.

.DESCRIPTION
  REPLACES the data in the database configured in backend\.env (and the recordings in
  <project>\uploads) with the data in the zip. Before anything changes it:
    - shows what the zip contains and asks you to type YES,
    - backs up your current database to data_exports\backup_before_import_<time>.sql,
    - moves your current recordings to uploads_backup_<time>.
  After importing it points every recording at this computer's project folder and checks
  that all tables and recordings arrived.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\import_data.ps1 -ZipFile "C:\Downloads\fmcg_data_20261012_101500.zip"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ZipFile,
    [switch]$Yes   # skip the confirmation (for scripts)
)
. (Join-Path $PSScriptRoot 'fmcg_data_common.ps1')

if (-not (Test-Path -LiteralPath $ZipFile)) { throw "Zip not found: $ZipFile" }
$ZipFile = (Resolve-Path -LiteralPath $ZipFile).Path
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stage = Join-Path ([IO.Path]::GetTempPath()) "fmcg_import_$stamp"
$optionFile = $null

try {
    Write-Step "Opening $([IO.Path]::GetFileName($ZipFile))"
    Expand-Archive -LiteralPath $ZipFile -DestinationPath $stage -Force
    $manifestPath = Join-Path $stage 'manifest.json'
    $sqlFile = Join-Path $stage 'database.sql'
    if (-not (Test-Path -LiteralPath $manifestPath) -or -not (Test-Path -LiteralPath $sqlFile)) {
        throw "This zip wasn't made with export_data.ps1 (manifest.json or database.sql is missing)."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $expected = [ordered]@{}
    foreach ($p in $manifest.table_counts.PSObject.Properties) { $expected[$p.Name] = [int64]$p.Value }
    Write-Note "Exported from $($manifest.computer) on $($manifest.created_at) (MySQL $($manifest.mysql_version))"
    Show-Counts $expected
    Write-Note "Recording files: $($manifest.recording_files)"

    Write-Step "This computer's database (backend\.env)"
    $db = Get-DbSettings
    $mysql = Find-MySqlTool 'mysql'
    $mysqldump = Find-MySqlTool 'mysqldump'
    $optionFile = New-MySqlOptionFile $db
    $exists = (Invoke-Sql $mysql $optionFile $null "SHOW DATABASES LIKE '$($db.Database)'").Trim() -ne ''
    Show-DbSettings $db
    if ($exists) {
        $current = Get-TableCounts $mysql $optionFile $db.Database
        Write-Note "It currently contains:"
        Show-Counts $current
    } else {
        Write-Note "It doesn't exist yet; it will be created."
    }

    if ($env:COMPUTERNAME -and $manifest.computer -eq $env:COMPUTERNAME -and $manifest.database -eq $db.Database) {
        Write-Warn "WARNING: this zip was exported from this computer's '$($db.Database)' database. Importing it here replaces that database."
    }
    if (-not $Yes) {
        Write-Host ""
        Write-Warn "This REPLACES the data in '$($db.Database)' and the recordings in $($script:UploadsDir)"
        Write-Warn "with the data from the zip. Your current data is backed up first."
        $answer = Read-Host "Type YES to continue"
        if ($answer -cne 'YES') { Write-Host "Cancelled. Nothing was changed."; return }
    }

    $backupDir = Join-Path $script:ProjectRoot 'data_exports'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    if ($exists) {
        Write-Step "Backing up your current database"
        $backupSql = Join-Path $backupDir "backup_before_import_$stamp.sql"
        $backupArgs = @("--defaults-extra-file=$optionFile", '--single-transaction', '--routines', '--triggers',
            '--no-tablespaces', '--hex-blob', '--default-character-set=utf8mb4', "--result-file=$backupSql")
        if ((Invoke-Tool $mysqldump @('--version')) -notmatch 'MariaDB') { $backupArgs += '--set-gtid-purged=OFF' }
        $backupArgs += $db.Database
        Invoke-Tool $mysqldump $backupArgs | Out-Null
        Write-Ok $backupSql
    }
    $uploadsBackup = $null
    if (Test-Path -LiteralPath $script:UploadsDir) {
        Write-Step "Moving your current recordings aside"
        $uploadsBackup = Join-Path $script:ProjectRoot "uploads_backup_$stamp"
        Move-Item -LiteralPath $script:UploadsDir -Destination $uploadsBackup
        Write-Ok $uploadsBackup
    }

    Write-Step "Importing the database"
    Invoke-Sql $mysql $optionFile $null "DROP DATABASE IF EXISTS ``$($db.Database)``; CREATE DATABASE ``$($db.Database)`` CHARACTER SET utf8mb4;" | Out-Null
    # MySQL's own 'source' reads the file correctly (PowerShell has no '<' and would mangle UTF-8).
    Invoke-Tool $mysql @("--defaults-extra-file=$optionFile", '--default-character-set=utf8mb4', $db.Database,
        '-e', "source $($sqlFile.Replace('\', '/'))") | Out-Null
    Write-Ok "Imported."

    Write-Step "Copying the recordings"
    $srcUploads = Join-Path $stage 'uploads'
    New-Item -ItemType Directory -Path $script:UploadsDir -Force | Out-Null
    if (Test-Path -LiteralPath $srcUploads) {
        Copy-Item -Path (Join-Path $srcUploads '*') -Destination $script:UploadsDir -Recurse -Force
    }
    foreach ($sub in @('videos', 'audio')) { New-Item -ItemType Directory -Path (Join-Path $script:UploadsDir $sub) -Force | Out-Null }
    Write-Ok $script:UploadsDir

    Write-Step "Pointing the recordings at this computer's project folder"
    # feedback_records.video_path holds full paths from the exporting computer, e.g.
    #   C:\Users\iONE IT\Documents\FMCG-Learning-Engine\uploads\videos\123_x.mp4
    # Keep the part from \uploads\ onwards and put this project's folder in front of it.
    $sep = [IO.Path]::DirectorySeparatorChar
    $rootSql = $script:ProjectRoot.TrimEnd('\', '/').Replace('\', '\\').Replace("'", "''")
    $sepSql = if ($sep -eq '\') { '\\' } else { '/' }
    $fixSql = @"
SET @root = '$rootSql';
SET @sep = '$sepSql';
UPDATE feedback_records
   SET video_path = CONCAT(@root, REPLACE(REPLACE(
         SUBSTRING(video_path, GREATEST(LOCATE('\\uploads\\', video_path), LOCATE('/uploads/', video_path))),
         '\\', @sep), '/', @sep))
 WHERE GREATEST(LOCATE('\\uploads\\', video_path), LOCATE('/uploads/', video_path)) > 0;
SELECT ROW_COUNT();
"@
    $fixFile = Join-Path $stage 'fix_paths.sql'
    Write-Utf8NoBom $fixFile $fixSql
    $updated = (Invoke-Tool $mysql @("--defaults-extra-file=$optionFile", '-N', '-B', $db.Database,
        '-e', "source $($fixFile.Replace('\', '/'))")).Trim()
    Write-Ok "$updated recording paths updated to $($script:ProjectRoot)"

    Write-Step "Checking the result"
    $after = Get-TableCounts $mysql $optionFile $db.Database
    $problems = 0
    foreach ($t in $expected.Keys) {
        $got = if ($after.Contains($t)) { $after[$t] } else { -1 }
        if ($got -ne $expected[$t]) { $problems++; Write-Warn ("{0}: expected {1}, found {2}" -f $t, $expected[$t], $got) }
    }
    if ($problems -eq 0) { Write-Ok "All $($expected.Count) tables match the export." }
    Show-Counts $after

    $paths = (Invoke-Sql $mysql $optionFile $db.Database "SELECT video_path FROM feedback_records WHERE video_path IS NOT NULL AND video_path <> ''") -split "`r?`n" | Where-Object { $_ -ne '' }
    $missing = @($paths | Where-Object { -not (Test-Path -LiteralPath $_) })
    if ($paths.Count -eq 0) { Write-Note "No recordings to check." }
    elseif ($missing.Count -eq 0) { Write-Ok "All $($paths.Count) recordings found on this computer." }
    else {
        Write-Warn "$($missing.Count) of $($paths.Count) recordings are missing (they weren't in the exporter's uploads folder):"
        $missing | Select-Object -First 5 | ForEach-Object { Write-Warn "  $_" }
    }

    Write-Host ""
    if ($problems -eq 0) { Write-Host "Import complete." -ForegroundColor Green } else { Write-Host "Import finished with differences (see above)." -ForegroundColor Yellow }
    Write-Host "Next:"
    Write-Host "  1. Restart the backend (Ctrl+C, then uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload)."
    Write-Host "  2. Sign in with an account from the exported data (your old local accounts were replaced)."
    if ($exists) { Write-Host "  To undo: import data_exports\backup_before_import_$stamp.sql and restore uploads_backup_$stamp." }
}
finally {
    if ($optionFile -and (Test-Path -LiteralPath $optionFile)) { Remove-Item -LiteralPath $optionFile -Force }
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}