<#
.SYNOPSIS
  Export the FMCG Learning Engine data into ONE zip file to share with your team.

.DESCRIPTION
  The zip contains:
    database.sql   the whole fmcg_learning database: categories, products, questions,
                   recipes, question templates, users, surveys, answers, analyses, reports
    uploads\       the recordings (<project>\uploads\videos and \audio)
    manifest.json  row counts, this project's folder and MySQL version (used by the import)

  It only READS: nothing on this computer is changed. The database settings come from
  backend\.env, exactly as the backend uses them.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\export_data.ps1
#>
[CmdletBinding()]
param(
    [string]$OutputFolder
)
. (Join-Path $PSScriptRoot 'fmcg_data_common.ps1')

if (-not $OutputFolder) { $OutputFolder = Join-Path $script:ProjectRoot 'data_exports' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stage = Join-Path ([IO.Path]::GetTempPath()) "fmcg_export_$stamp"
$optionFile = $null

try {
    Write-Step "Reading database settings from backend\.env"
    $db = Get-DbSettings
    Show-DbSettings $db
    $mysql = Find-MySqlTool 'mysql'
    $mysqldump = Find-MySqlTool 'mysqldump'
    $optionFile = New-MySqlOptionFile $db

    Write-Step "Checking the database"
    $version = (Invoke-Sql $mysql $optionFile $null 'SELECT VERSION()').Trim()
    $counts = Get-TableCounts $mysql $optionFile $db.Database
    if (-not $counts.Contains('products')) { throw "Database '$($db.Database)' has no FMCG tables. Check FMCG_DB_NAME in backend\.env." }
    Write-Note "MySQL $version"
    Show-Counts $counts

    Write-Step "Exporting the database"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $sqlFile = Join-Path $stage 'database.sql'
    $dumpArgs = @(
        "--defaults-extra-file=$optionFile",   # must be the first option
        '--single-transaction',                # consistent snapshot, no locking
        '--routines', '--triggers',
        '--no-tablespaces',                    # avoids a PROCESS-privilege error on MySQL 8
        '--hex-blob',
        '--default-character-set=utf8mb4',     # keeps Hindi/Gujarati/... text intact
        "--result-file=$sqlFile"               # written by mysqldump itself (PowerShell '>' would corrupt it)
    )
    $toolVersion = Invoke-Tool $mysqldump @('--version')
    if ($toolVersion -notmatch 'MariaDB') { $dumpArgs += '--set-gtid-purged=OFF' }
    $dumpArgs += $db.Database
    Invoke-Tool $mysqldump $dumpArgs | Out-Null
    $sqlSize = (Get-Item -LiteralPath $sqlFile).Length
    if ($sqlSize -lt 1000) { throw "The database export is unexpectedly small ($sqlSize bytes)." }
    Write-Ok ("database.sql  {0:N1} MB" -f ($sqlSize / 1MB))

    Write-Step "Copying the recordings"
    $recCount = 0; $recBytes = 0
    if (Test-Path -LiteralPath $script:UploadsDir) {
        $target = Join-Path $stage 'uploads'
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        foreach ($sub in @('videos', 'audio')) {
            $src = Join-Path $script:UploadsDir $sub
            if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $target -Recurse -Force }
        }
        $files = Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction SilentlyContinue
        $recCount = @($files).Count
        $recBytes = ($files | Measure-Object -Property Length -Sum).Sum
        if (-not $recBytes) { $recBytes = 0 }
        Write-Ok ("{0} files, {1:N1} MB (from {2})" -f $recCount, ($recBytes / 1MB), $script:UploadsDir)
    } else {
        Write-Warn "No recordings folder at $($script:UploadsDir); the export contains the database only."
    }

    Write-Step "Writing the manifest"
    $manifest = [ordered]@{
        format          = 'fmcg-data-export/1'
        created_at      = (Get-Date).ToString('s')
        computer        = $env:COMPUTERNAME
        database        = $db.Database
        mysql_version   = $version
        project_root    = $script:ProjectRoot
        uploads_folder  = $script:UploadsDir
        recording_files = $recCount
        table_counts    = $counts
    }
    Write-Utf8NoBom (Join-Path $stage 'manifest.json') ($manifest | ConvertTo-Json -Depth 4)

    Write-Step "Creating the zip"
    New-Item -ItemType Directory -Path $OutputFolder -Force | Out-Null
    $zip = Join-Path (Resolve-Path $OutputFolder).Path "fmcg_data_$stamp.zip"
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
    Write-Ok ("{0}  ({1:N1} MB)" -f $zip, ((Get-Item -LiteralPath $zip).Length / 1MB))

    Write-Host ""
    Write-Host "Done. Share this file with your team:" -ForegroundColor Green
    Write-Host "  $zip"
    Write-Host ""
    Write-Warn "It contains customer names and phone numbers, recordings and password hashes:"
    Write-Warn "share it privately (not in GitHub, not in public chats)."
    $gitignore = Join-Path $script:ProjectRoot '.gitignore'
    if (-not (Test-Path -LiteralPath $gitignore) -or -not (Select-String -LiteralPath $gitignore -Pattern '^/?data_exports/?$' -Quiet)) {
        Write-Warn "Add the line  data_exports/  to .gitignore so exports are never committed."
    }
    Write-Host "Your teammates run:  powershell -ExecutionPolicy Bypass -File .\scripts\import_data.ps1 -ZipFile <path to the zip>"
}
finally {
    if ($optionFile -and (Test-Path -LiteralPath $optionFile)) { Remove-Item -LiteralPath $optionFile -Force }
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}