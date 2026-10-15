# ============================================================
# Database backup for the Windows (non-Docker) setup  (OPS-01)
# ============================================================
# Run from the project root in PowerShell:
#     .\scripts\backup_mysql.ps1
#     .\scripts\backup_mysql.ps1 -BackupDir "D:\fmcg-backups" -KeepDays 30
#
# Reads MYSQL_HOST / MYSQL_PORT / MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD
# from backend\.env (the same settings the backend uses). Writes
#     <BackupDir>\db\fmcg_<date>.sql            consistent dump (no table locks)
#     <BackupDir>\recordings\                   copy of new recordings (nothing overwritten)
# and deletes dumps older than -KeepDays.
#
# Schedule it daily with Task Scheduler (Action: powershell.exe
#   -ExecutionPolicy Bypass -File "C:\Users\iONE IT\FMCG-Learning-Engine\scripts\backup_mysql.ps1").
# Put BackupDir on ANOTHER disk or a synced cloud folder.
# Restore:  mysql -u root -p fmcg_learning < D:\fmcg-backups\db\fmcg_<date>.sql
param(
    [string]$BackupDir = (Join-Path $PSScriptRoot "..\backups"),
    [int]$KeepDays = 14,
    [string]$MysqlBin = ""
)
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $root "backend\.env"
$settings = @{ MYSQL_HOST = "127.0.0.1"; MYSQL_PORT = "3306"; MYSQL_DATABASE = "fmcg_learning"; MYSQL_USER = "root"; MYSQL_PASSWORD = $null }
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*(MYSQL_[A-Z_]+)\s*=\s*(.*)\s*$') { $settings[$Matches[1]] = $Matches[2].Trim('"', "'") }
    }
}
foreach ($name in @($settings.Keys)) {
    $fromEnv = [Environment]::GetEnvironmentVariable($name)
    if ($fromEnv) { $settings[$name] = $fromEnv }
}
if (-not $settings.MYSQL_PASSWORD) { throw "MYSQL_PASSWORD is not set in backend\.env or the environment." }

# Find mysqldump.exe (PATH, or the standard MySQL Server install folders).
$dump = if ($MysqlBin) { Join-Path $MysqlBin "mysqldump.exe" } else { (Get-Command mysqldump.exe -ErrorAction SilentlyContinue).Source }
if (-not $dump) {
    $dump = Get-ChildItem "C:\Program Files\MySQL" -Recurse -Filter mysqldump.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $dump) { throw "mysqldump.exe not found. Pass -MysqlBin 'C:\Program Files\MySQL\MySQL Server 8.4\bin'." }

$dbDir = Join-Path $BackupDir "db"
New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$target = Join-Path $dbDir "fmcg_$stamp.sql"
$partial = "$target.part"

Write-Host "Dumping $($settings.MYSQL_DATABASE) from $($settings.MYSQL_HOST):$($settings.MYSQL_PORT) ..."
# The password goes in a temporary option file readable only by this user, never on
# the command line (where other programs could see it in the process list).
$optionFile = Join-Path $env:TEMP ("fmcg_backup_{0}.cnf" -f [guid]::NewGuid())
Set-Content -Path $optionFile -Value "[client]`npassword=`"$($settings.MYSQL_PASSWORD)`"" -Encoding ASCII
try {
    & $dump "--defaults-extra-file=$optionFile" "--host=$($settings.MYSQL_HOST)" "--port=$($settings.MYSQL_PORT)" "--user=$($settings.MYSQL_USER)" `
        --single-transaction --quick --routines --triggers --no-tablespaces --default-character-set=utf8mb4 `
        "--result-file=$partial" $settings.MYSQL_DATABASE
    if ($LASTEXITCODE -ne 0) { throw "mysqldump failed with exit code $LASTEXITCODE." }
} finally {
    Remove-Item $optionFile -Force -ErrorAction SilentlyContinue
}
Move-Item $partial $target -Force
Write-Host ("Database dump OK: {0} ({1:N1} MB)" -f $target, ((Get-Item $target).Length / 1MB))

# Recordings: copy new files only.
$recordings = Join-Path $root "uploads"
if (Test-Path $recordings) {
    $copyTo = Join-Path $BackupDir "recordings"
    robocopy $recordings $copyTo /E /XC /XN /XO /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying recordings failed (robocopy exit $LASTEXITCODE)." }
    Write-Host "Recordings copied to $copyTo"
}

Get-ChildItem $dbDir -Filter "fmcg_*.sql" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } | Remove-Item
Set-Content -Path (Join-Path $BackupDir "LAST_SUCCESS.txt") -Value (Get-Date -Format o)
Write-Host "Backup finished."
