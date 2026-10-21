# ============================================================
# Android app ID and app name (Play Store readiness)
# ============================================================
# The Play Store refuses apps whose ID starts with "com.example". This script
# changes, in the mobile app's Android settings:
#   - applicationId   (the app's permanent identity on the Play Store and on phones)
#   - android:label   (the name under the app icon)
# The Kotlin "namespace" and MainActivity package stay as they are: they are
# internal to the code and do not need to match the app ID.
#
# Run from the project root in PowerShell:
#     .\scripts\set_android_app_id.ps1
#     .\scripts\set_android_app_id.ps1 -AppId "com.ioneit.recipesurvey" -AppName "Recipe Survey"
#     .\scripts\set_android_app_id.ps1 -WhatIf          (only shows what would change)
#
# Choose the ID carefully: once the app is on the Play Store it can NEVER change.
# Usual form: your domain backwards + app name, lower case, e.g. com.ioneit.recipesurvey
#
# After changing it the app installs as a NEW app: remove the old one from test
# phones/emulators once (adb uninstall com.example.mobile) and sign in again.
# A backup of each changed file is saved next to it (*.bak).
param(
    [string]$AppId = "com.ioneit.recipesurvey",
    [string]$AppName = "Recipe Survey",
    [switch]$WhatIf
)
$ErrorActionPreference = "Stop"

if ($AppId -notmatch '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$') {
    throw "AppId '$AppId' is not valid: use lower-case words separated by dots, e.g. com.ioneit.recipesurvey"
}
if ($AppId -like "com.example*") { throw "The Play Store refuses IDs starting with com.example. Choose your own." }

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$android = Join-Path $root "mobile\android\app"
$gradle = @("build.gradle.kts", "build.gradle") | ForEach-Object { Join-Path $android $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
$manifest = Join-Path $android "src\main\AndroidManifest.xml"
if (-not $gradle) { throw "mobile\android\app\build.gradle(.kts) not found. Run this from the project root." }
if (-not (Test-Path $manifest)) { throw "mobile\android\app\src\main\AndroidManifest.xml not found." }

function Update-File($path, $pattern, $replacement, $label) {
    $raw = [IO.File]::ReadAllText($path)
    $match = [regex]::Match($raw, $pattern)
    if (-not $match.Success) { Write-Warning "$label not found in $path (nothing changed)"; return }
    $updated = ([regex]$pattern).Replace($raw, $replacement, 1)  # first match only
    if ($updated -eq $raw) { Write-Host "  $label already set" -ForegroundColor DarkGray; return }
    $before = ($match.Value -replace '\s+', ' ') -replace '^<application ', ''
    $after = (([regex]::Match($updated, $pattern)).Value -replace '\s+', ' ') -replace '^<application ', ''
    Write-Host ("  {0}  ->  {1}" -f $before.Trim(), $after.Trim()) -ForegroundColor Cyan
    if (-not $WhatIf) {
        Copy-Item $path "$path.bak" -Force
        # Keep the file's own encoding (UTF-8 without BOM) and line endings.
        [IO.File]::WriteAllText($path, $updated, (New-Object Text.UTF8Encoding($false)))
    }
}

Write-Host "Android settings ($gradle):"
# Kotlin DSL:  applicationId = "com.example.mobile"     Groovy:  applicationId "com.example.mobile"
Update-File $gradle '(applicationId\s*=?\s*)"[^"]*"' "`$1`"$AppId`"" "applicationId"

Write-Host "App name ($manifest):"
$safeName = [Security.SecurityElement]::Escape($AppName)
Update-File $manifest '(<application[^>]*?android:label\s*=\s*)"[^"]*"' "`$1`"$safeName`"" "android:label"

if ($WhatIf) {
    Write-Host "`n-WhatIf: nothing was written." -ForegroundColor Yellow
} else {
    Write-Host "`nDone. Next:" -ForegroundColor Green
    Write-Host "  cd mobile"
    Write-Host "  flutter clean"
    Write-Host "  adb uninstall com.example.mobile     (old test install, once)"
    Write-Host "  flutter run"
}
