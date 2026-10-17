# Installer smoke test (#71), run by .github/workflows/desktop.yml on a
# Windows runner. Installs silently, checks the installed files, drives the
# installed backend (smoke-backend.js), launches the real app in mock mode and
# quits it, checks nothing is left running, then uninstalls silently.
#
#   pwsh desktop/scripts/ci-smoke.ps1 -Installer desktop/release/LyreTeXt-Setup-<version>.exe
#
# Not meant for a developer machine: it installs into and removes from the
# current user's profile, and deletes %APPDATA%\LyreTeXt\backend.log.
param(
  [Parameter(Mandatory = $true)] [string] $Installer,
  [string] $Scratch = $(if ($env:RUNNER_TEMP) { Join-Path $env:RUNNER_TEMP 'lyretext-smoke' } else { Join-Path ([IO.Path]::GetTempPath()) 'lyretext-smoke' })
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$summary = [System.Collections.Generic.List[string]]::new()

function Step($message) { Write-Host "`n=== $message" }
function Ok($message) { Write-Host "ok   $message" }
function Fail($message) { throw "FAIL: $message" }
function AppProcesses { @(Get-Process -Name 'LyreTeXt', 'lyretext-backend', 'pandoc' -ErrorAction SilentlyContinue) }
function WaitUntil([scriptblock] $cond, [int] $seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) { if (& $cond) { return $true }; Start-Sleep -Milliseconds 500 }
  return [bool](& $cond)
}

New-Item -ItemType Directory -Force $Scratch | Out-Null
if (AppProcesses) { Fail "LyreTeXt processes are already running before the test: $(AppProcesses | ForEach-Object Name)" }

# --- 1. silent install -------------------------------------------------------
Step 'Silent install (per-user, no elevation)'
$installer = (Resolve-Path $Installer).Path
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $installer -ArgumentList '/S' -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "installer exited with $($p.ExitCode)" }
Ok "installer exited 0 in $([math]::Round($sw.Elapsed.TotalSeconds, 1)) s"
$summary.Add("| Silent install | $([math]::Round($sw.Elapsed.TotalSeconds, 1)) s |")

# A per-user NSIS install lands under %LOCALAPPDATA%\Programs.
$exe = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs') -Filter 'LyreTeXt.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) { Fail 'LyreTeXt.exe not found under %LOCALAPPDATA%\Programs' }
$installDir = $exe.DirectoryName
Ok "installed to $installDir"
# A silent install should not launch the app; stop it if it did, so the
# single-instance lock does not swallow the launch below.
if (WaitUntil { (AppProcesses).Count -gt 0 } 3) {
  Write-Host 'note: the installer launched the app; closing it'
  AppProcesses | Stop-Process -Force
  Start-Sleep 3
}

$resources = Join-Path $installDir 'resources'
foreach ($rel in 'app.asar', 'backend\lyretext-backend.exe', 'toolchain\pandoc\pandoc.exe',
                 'toolchain\pretext-writer\pretext.lua', 'toolchain\pretext-writer\pretext-environments.lua',
                 'toolchain\NOTICE.txt', 'toolchain\toolchain.json', 'toolchain\pandoc\SOURCE.txt',
                 'toolchain\pretext-writer\LICENSE', 'LICENSE.txt') {
  if (-not (Test-Path (Join-Path $resources $rel))) { Fail "missing resources\$rel" }
}
Ok 'backend, toolchain and licence/notice files are installed'
$fixture = Get-ChildItem (Join-Path $resources 'backend') -Recurse -Filter 'chapter-1-introduction.ptx' | Select-Object -First 1
if (-not $fixture) { Fail 'mock fixture is not in the frozen backend' }
Ok "mock fixture bundled ($($fixture.FullName.Substring($resources.Length + 1)))"

# --- 2. installed backend: health, environment, mock run, stop ---------------
Step 'Installed backend (smoke-backend.js under the app''s Electron)'
$out = Join-Path $Scratch 'smoke-backend.out.txt'
$err = Join-Path $Scratch 'smoke-backend.err.txt'
$env:ELECTRON_RUN_AS_NODE = '1'
try {
  $p = Start-Process -FilePath $exe.FullName -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -ArgumentList @("`"$(Join-Path $here 'smoke-backend.js')`"", '--resources', "`"$resources`"", '--data', "`"$(Join-Path $Scratch 'backend')`"")
} finally {
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
}
Get-Content $out, $err -ErrorAction SilentlyContinue | Write-Host
if ($p.ExitCode -ne 0) { Fail "smoke-backend.js exited with $($p.ExitCode)" }
$healthyLine = Select-String -Path $out -Pattern 'healthy at .* after ([\d.]+) s' | Select-Object -First 1
if ($healthyLine) { $summary.Add("| Backend start (installed, ELECTRON_RUN_AS_NODE) | $($healthyLine.Matches[0].Groups[1].Value) s |") }

# --- 3. the real app: launch in mock mode, wait for health, quit -------------
Step 'Launch the installed app (LYRETEXT_MOCK=1) and quit it'
$userData = Join-Path $env:APPDATA 'LyreTeXt'
$log = Join-Path $userData 'backend.log'
Remove-Item $log -ErrorAction SilentlyContinue
$env:LYRETEXT_MOCK = '1'
$sw = [Diagnostics.Stopwatch]::StartNew()
$app = Start-Process -FilePath $exe.FullName -PassThru
Remove-Item Env:LYRETEXT_MOCK
$script:port = $null
$healthy = WaitUntil {
  if (-not $script:port -and (Test-Path $log)) {
    $m = Select-String -Path $log -Pattern 'on port (\d+)' | Select-Object -Last 1
    if ($m) { $script:port = $m.Matches[0].Groups[1].Value }
  }
  if (-not $script:port) { return $false }
  try {
    (Invoke-WebRequest "http://127.0.0.1:$($script:port)/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
  } catch { $false }
} 180
if (-not $healthy) {
  if (Test-Path $log) { Get-Content $log -Tail 60 | Write-Host }
  Fail 'the app''s backend did not become healthy within 180 s'
}
$launch = [math]::Round($sw.Elapsed.TotalSeconds, 1)
Ok "app backend healthy on port $($script:port) after $launch s (first launch of a fresh install)"
$summary.Add("| App launch to healthy backend (first launch) | $launch s |")
# Let the main window load the UI, then ask it to close the way a user would:
# WM_CLOSE to its windows (taskkill without /F), which runs the app's normal
# quit path (window-all-closed -> will-quit -> backend.stop()).
Start-Sleep 5
& taskkill /PID $app.Id | Out-Host
$graceful = WaitUntil { (AppProcesses).Count -eq 0 } 30
if ($graceful) {
  Ok 'app quit on WM_CLOSE and took its backend with it'
  $summary.Add('| Quit | graceful (WM_CLOSE), no processes left |')
} else {
  # A runner session may not deliver WM_CLOSE to the window. Fall back to a
  # hard kill of Electron alone (no /T): the backend's stdin watchdog must
  # still take the backend down.
  Write-Host "::warning::LyreTeXt did not quit on WM_CLOSE within 30 s; hard-killing Electron only, to test the backend watchdog"
  & taskkill /PID $app.Id /F | Out-Host
  if (-not (WaitUntil { (AppProcesses).Count -eq 0 } 30)) {
    AppProcesses | Format-Table Id, Name, StartTime | Out-Host
    Fail 'processes remain 30 s after Electron was killed (backend watchdog failed)'
  }
  Ok 'after a hard kill of Electron, the backend watchdog exited the backend'
  $summary.Add('| Quit | WM_CLOSE not delivered; hard kill, watchdog cleaned up |')
}
Start-Sleep 2
if (AppProcesses) { Fail "processes remain: $(AppProcesses | ForEach-Object { "$($_.Name)($($_.Id))" })" }
Ok 'no LyreTeXt, lyretext-backend or pandoc processes remain'

# --- 4. silent uninstall -----------------------------------------------------
Step 'Silent uninstall'
$uninstaller = Join-Path $installDir 'Uninstall LyreTeXt.exe'
if (-not (Test-Path $uninstaller)) { Fail "uninstaller not found: $uninstaller" }
$p = Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "uninstaller exited with $($p.ExitCode)" }
# The NSIS uninstaller re-launches itself from %TEMP%, so wait for the files.
if (-not (WaitUntil { -not (Test-Path $exe.FullName) } 120)) { Fail 'LyreTeXt.exe still present 120 s after uninstall' }
Ok "uninstalled ($installDir no longer has LyreTeXt.exe)"
$summary.Add('| Silent uninstall | ok |')

if ($env:GITHUB_STEP_SUMMARY) {
  @('### Installer smoke test', '', '| Step | Result |', '|---|---|') + $summary | Add-Content $env:GITHUB_STEP_SUMMARY
}
Write-Host "`nInstaller smoke test passed"
