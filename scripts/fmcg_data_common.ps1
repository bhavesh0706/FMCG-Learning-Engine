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
    # Exactly the rules of backend\app\config.py:
    #   - a real environment variable wins over the same name in backend\.env;
    #   - MYSQL_* names first, the older FMCG_DB_* names as fallback;
    #   - no default password: if it's missing, stop (as the backend does).
    $envFile = Join-Path $script:BackendDir '.env'
    $file = Read-DotEnv $envFile
    $sources = [ordered]@{}
    function Lookup([string]$Name) {
        $fromEnv = [Environment]::GetEnvironmentVariable($Name)
        if ($fromEnv -and $fromEnv.Trim()) { return @{ Value = $fromEnv; Source = "environment variable $Name" } }
        if ($file.ContainsKey($Name) -and $file[$Name].Trim()) { return @{ Value = $file[$Name]; Source = "backend\.env ($Name)" } }
        return $null
    }
    function Pick([string]$Key, [string[]]$Names, [string]$Default) {
        foreach ($n in $Names) {
            $hit = Lookup $n
            if ($hit) { $sources[$Key] = $hit.Source; return $hit.Value.Trim() }
        }
        $sources[$Key] = 'default'
        return $Default
    }
    $settings = [ordered]@{
        Host     = Pick 'host' @('MYSQL_HOST', 'FMCG_DB_HOST') '127.0.0.1'
        Port     = Pick 'port' @('MYSQL_PORT', 'FMCG_DB_PORT') '3306'
        Database = Pick 'database' @('MYSQL_DATABASE', 'FMCG_DB_NAME') 'fmcg_learning'
        User     = Pick 'user' @('MYSQL_USER', 'FMCG_DB_USER') 'root'
        Password = $null
        EnvFile  = $envFile
        Sources  = $sources
    }
    foreach ($n in @('MYSQL_PASSWORD', 'FMCG_DB_PASSWORD')) {
        $hit = Lookup $n
        if ($hit) { $settings.Password = $hit.Value; $sources['password'] = $hit.Source; break }  # not trimmed, like config.py
    }
    if (-not $settings.Password) {
        throw "MYSQL_PASSWORD is not set. Add this line to $envFile (with your MySQL password):`n    MYSQL_PASSWORD=your-mysql-password"
    }
    $portNumber = 0
    if (-not [int]::TryParse([string]$settings.Port, [ref]$portNumber) -or $portNumber -lt 1 -or $portNumber -gt 65535) {
        throw ("MySQL port must be a number between 1 and 65535, got '{0}' (from {1})." -f $settings.Port, $sources['port'])
    }
    if ($settings.Database -notmatch '^[A-Za-z0-9_]+$') {
        throw ("Database name may only contain letters, digits and _, got '{0}' (from {1})." -f $settings.Database, $sources['database'])
    }
    return $settings
}

function Show-DbSettings($Db) {
    # Where the connection settings came from. A leftover $env:MYSQL_* / $env:FMCG_DB_* in this
    # PowerShell window overrides backend\.env (just like for the backend itself).
    Write-Note ("Database: {0} on {1}:{2} (user {3})" -f $Db.Database, $Db.Host, $Db.Port, $Db.User)
    foreach ($k in @('database', 'host')) { Write-Note ("  {0} from {1}" -f $k, $Db.Sources[$k]) }
    $fromEnvVars = @($Db.Sources.Keys | Where-Object { $_ -ne 'password' -and $Db.Sources[$_] -like 'environment variable*' } |
        ForEach-Object { ($Db.Sources[$_] -split ' ')[-1] })
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