@echo off
rem Checks every file of Secure Offline Transcriber against SHA256SUMS.txt.
rem Use it after unzipping: it finds files that are missing (unzipping stopped, or antivirus
rem blocked a file) or changed. It takes about half a minute and changes nothing.
setlocal
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$sotText=[IO.File]::ReadAllText($env:SELF); $sotMark='#__'+'POWERSHELL__'; $global:rc=1; & ([ScriptBlock]::Create($sotText.Substring($sotText.LastIndexOf($sotMark) + $sotMark.Length))); exit $global:rc"
echo.
pause
exit /b %errorlevel%

#__POWERSHELL__
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$root = $env:APPDIR
$list = Join-Path $root 'SHA256SUMS.txt'
if (-not (Test-Path -LiteralPath $list)) { Write-Host 'SHA256SUMS.txt is missing.' -ForegroundColor Red; return }
$missing = New-Object System.Collections.Generic.List[string]
$changed = New-Object System.Collections.Generic.List[string]
$lines = [IO.File]::ReadAllLines($list)
$sha = [Security.Cryptography.SHA256]::Create()
$i = 0
foreach ($line in $lines) {
    $i++
    if ($line.Length -lt 67) { continue }
    $want = $line.Substring(0, 64).ToLower()
    $rel = $line.Substring(66)
    $path = Join-Path $root ($rel -replace '/', '\')
    if ($i % 200 -eq 0) { Write-Progress -Activity 'Checking files' -Status $rel -PercentComplete ($i * 100 / $lines.Count) }
    if (-not (Test-Path -LiteralPath $path)) { $missing.Add($rel); continue }
    $fs = [IO.File]::OpenRead($path)
    try { $got = -join ($sha.ComputeHash($fs) | ForEach-Object { $_.ToString('x2') }) } finally { $fs.Close() }
    if ($got -ne $want) { $changed.Add($rel) }
}
Write-Progress -Activity 'Checking files' -Completed
Write-Host ('Checked ' + $lines.Count + ' files.')
if ($missing.Count -eq 0 -and $changed.Count -eq 0) {
    Write-Host 'All files are complete and unchanged.' -ForegroundColor Green
    $global:rc = 0
    return
}
if ($missing.Count) {
    Write-Host ('' + $missing.Count + ' file(s) are MISSING, for example:') -ForegroundColor Red
    $missing | Select-Object -First 15 | ForEach-Object { Write-Host ('  ' + $_) }
}
if ($changed.Count) {
    Write-Host ('' + $changed.Count + ' file(s) are DIFFERENT from the original:') -ForegroundColor Red
    $changed | Select-Object -First 15 | ForEach-Object { Write-Host ('  ' + $_) }
}
Write-Host ''
Write-Host 'Delete this folder and unzip the zip again (7-Zip is the most reliable).'
Write-Host 'If it happens again, check Windows Security > Virus and threat protection > Protection history.'
