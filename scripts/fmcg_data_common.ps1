# Shared helpers for export_data.ps1 and import_data.ps1.
# Works in Windows PowerShell 5.1 and PowerShell 7+.

$ErrorActionPreference = 'Stop'

# The project folder (this file lives in <project>\scripts).
$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:BackendDir = Join-Path $script:ProjectRoot 'backend'
# Recordings: video.py saves them in <project>\uploads\videos (and audio in <project>\uploads\audio).
$script:UploadsDir = Join-Path $script:ProjectRoot 'uploads'

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "    $Text" }
function Write-Warn([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    # MySQL can't read option/SQL files that start with a byte-order mark.
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $eq = $t.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $t.Substring(0, $eq).Trim()
        $val = $t.Substring($eq + 1).Trim()
        if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'")))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        $values[$key] = $val
    }
    return $values
}

function Get-DbSettings {
    # Same rules as backend\app\database\database.py: environment variables first,
    # then backend\.env, then the defaults.
    $envFile = Join-Path $script:BackendDir '.env'
    $file = Read-DotEnv $envFile
    $sources = [ordered]@{}
    function Pick([string]$Name, [string]$Default) {
        $fromEnv = [Environment]::GetEnvironmentVariable($Name)
        if ($fromEnv) { $sources[$Name] = "environment variable $Name"; return $fromEnv }
        if ($file.ContainsKey($Name) -and $file[$Name] -ne '') { $sources[$Name] = 'backend\.env'; return $file[$Name] }
        $sources[$Name] = 'default'
        return $Default
    }
    $settings = [ordered]@{
        Host     = Pick 'FMCG_DB_HOST' '127.0.0.1'
        Port     = Pick 'FMCG_DB_PORT' '3306'
        Database = Pick 'FMCG_DB_NAME' 'fmcg_learning'
        User     = Pick 'FMCG_DB_USER' 'root'
        Password = Pick 'FMCG_DB_PASSWORD' $null
        EnvFile  = $envFile
        Sources  = $sources
    }
    if (-not $settings.Password) {
        $secure = Read-Host "MySQL password for user '$($settings.User)' (not found in backend\.env)" -AsSecureString
        $settings.Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    if ($settings.Database -notmatch '^[A-Za-z0-9_]+$') { throw "Unexpected database name '$($settings.Database)' in backend\.env." }
    return $settings
}

function Show-DbSettings($Db) {
    # Where the connection settings came from: a leftover $env:FMCG_DB_* in this PowerShell
    # window overrides backend\.env (just like for the backend itself).
    Write-Note ("Database: {0} on {1}:{2} (user {3})" -f $Db.Database, $Db.Host, $Db.Port, $Db.User)
    $fromEnvVars = @($Db.Sources.Keys | Where-Object { $Db.Sources[$_] -like 'environment variable*' -and $_ -ne 'FMCG_DB_PASSWORD' })
    foreach ($k in @('FMCG_DB_NAME', 'FMCG_DB_HOST')) { Write-Note ("  {0} from {1}" -f $k, $Db.Sources[$k]) }
    if ($fromEnvVars.Count) {
        Write-Warn ("WARNING: {0} set in this PowerShell window override backend\.env." -f ($fromEnvVars -join ', '))
        Write-Warn ("If that's not intended, close this window (or run: Remove-Item Env:{0}) and start again." -f ($fromEnvVars -join ', Env:'))
    }
}

function Find-MySqlTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $patterns = @(
        "C:\Program Files\MySQL\*\bin\$Name.exe",
        "C:\Program Files (x86)\MySQL\*\bin\$Name.exe",
        "C:\Program Files\MariaDB*\bin\$Name.exe",
        "C:\xampp\mysql\bin\$Name.exe",
        "C:\wamp64\bin\mysql\*\bin\$Name.exe"
    )
    foreach ($p in $patterns) {
        $found = Get-ChildItem -Path $p -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw "$Name.exe was not found. Add MySQL's bin folder to PATH, e.g.:  `$env:Path += ';C:\Program Files\MySQL\MySQL Server 8.0\bin'"
}

function New-MySqlOptionFile($Db) {
    # The password goes in a temporary option file (deleted afterwards), never on the command line.
    $path = [IO.Path]::Combine([IO.Path]::GetTempPath(), "fmcg_my_$([Guid]::NewGuid().ToString('N')).cnf")
    $pw = $Db.Password.Replace('\', '\\').Replace('"', '\"')
    $text = "[client]`nuser=$($Db.User)`npassword=`"$pw`"`nhost=$($Db.Host)`nport=$($Db.Port)`ndefault-character-set=utf8mb4`n"
    Write-Utf8NoBom $path $text
    return $path
}

function Invoke-Tool {
    # Runs mysql/mysqldump safely: arguments with spaces are quoted, warnings on stderr
    # don't stop the script, and a non-zero exit code does (with MySQL's own message).
    param([string]$Exe, [string[]]$Arguments)
    $quoted = foreach ($a in $Arguments) { if ($a -match '[\s"]') { '"' + $a.Replace('"', '\"') + '"' } else { $a } }
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ($quoted -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $p = [Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $p.WaitForExit()
    $stderr = $err.Result.Trim()
    if ($p.ExitCode -ne 0) {
        $tool = [IO.Path]::GetFileNameWithoutExtension($Exe)
        if ($stderr -match 'Unknown collation') {
            throw "$tool failed: $stderr`n    The data was exported from a newer MySQL than this one. Install MySQL 8.0 (or newer) here, then run the import again."
        }
        throw "$tool failed (exit code $($p.ExitCode)): $stderr"
    }
    return $out.Result
}

function Invoke-Sql($MySql, [string]$OptionFile, [string]$Database, [string]$Sql) {
    # -N no headers, -B tab-separated, --raw: values exactly as stored (no \\ escaping of Windows paths)
    $toolArgs = @("--defaults-extra-file=$OptionFile", '-N', '-B', '--raw')
    if ($Database) { $toolArgs += $Database }
    $toolArgs += @('-e', $Sql)
    return (Invoke-Tool $MySql $toolArgs)
}

function Get-TableCounts($MySql, [string]$OptionFile, [string]$Database) {
    $tables = (Invoke-Sql $MySql $OptionFile $Database 'SHOW TABLES') -split "`r?`n" | Where-Object { $_ -ne '' }
    $counts = [ordered]@{}
    if (-not $tables) { return $counts }
    $union = ($tables | ForEach-Object { "SELECT '$_', COUNT(*) FROM ``$_``" }) -join ' UNION ALL '
    foreach ($row in ((Invoke-Sql $MySql $OptionFile $Database $union) -split "`r?`n")) {
        if ($row -eq '') { continue }
        $parts = $row -split "`t"
        $counts[$parts[0]] = [int64]$parts[1]
    }
    return $counts
}

function Show-Counts($Counts, [string[]]$Order) {
    $labels = [ordered]@{
        categories = 'Categories'; products = 'Products'; survey_questions = 'Survey questions'
        question_templates = 'Question templates'; users = 'Users'; respondents = 'Customers'
        survey_responses = 'Surveys'; survey_answers = 'Survey answers'
        feedback_records = 'Analyzed recordings'; consolidated_reports = 'Consolidated reports'
    }
    foreach ($k in $labels.Keys) {
        if ($Counts.Contains($k)) { Write-Note ("{0,-22}{1,8}" -f $labels[$k], $Counts[$k]) }
    }
}