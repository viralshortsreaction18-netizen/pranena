# Windows PowerShell script for restoring CleanYourTV debloat, launcher handover, and Private DNS.
# Pre-configured for TV_HOST=172.30.102.100 (model hi352l).
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ArgsList
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$root = $PSScriptRoot
$cyt = Join-Path $root 'cleanyourtv'
if (-not $env:TV_HOST -or -not $env:TV_HOST.Trim()) { $env:TV_HOST = '172.30.102.100' }
$env:TV_HOST = $env:TV_HOST.Trim()
$tvHost = $env:TV_HOST

if ($ArgsList.Count -gt 0 -and ($ArgsList[0] -eq '--help' -or $ArgsList[0] -eq '-h')) {
    Write-Host 'Usage:'
    Write-Host '  restore.bat [debloat.txt] [stock.launcher.package]   Full restore of all disabled packages & stock launcher'
    Write-Host '  restore.bat --enable <package.name>                  Re-enable a single disabled app'
    Write-Host '  restore.bat --enable-voice                           Re-enable Google Assistant & Alexa'
    Write-Host '  restore.bat --reset-dns                              Revert Private DNS to default (opportunistic)'
    exit 0
}

$py = Join-Path $cyt 'tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    throw 'Run build-and-install.bat first to initialize cleanyourtv\tools\venv.'
}
$tvPy = Join-Path $cyt 'tools\tv.py'
$restorePs1 = Join-Path $cyt 'tools\restore.ps1'

if ($ArgsList.Count -gt 0) {
    switch ($ArgsList[0]) {
        '--enable' {
            if ($ArgsList.Count -lt 2) { throw 'Usage: restore.bat --enable <package.name>' }
            $pkg = $ArgsList[1]
            Write-Host "Re-enabling $pkg on $tvHost..."
            & $py -I $tvPy "pm enable --user 0 $pkg"
            exit 0
        }
        '--enable-voice' {
            Write-Host 'Re-enabling Voice Assistant packages...'
            foreach ($pkg in @('com.google.android.katniss', 'com.google.android.speech.pumpkin', 'org.droidtv.amazonalexa')) {
                $ErrorActionPreference = 'Continue'
                & $py -I $tvPy "pm enable --user 0 $pkg" 2>$null
                $ErrorActionPreference = 'Stop'
            }
            Write-Host 'Done.'
            exit 0
        }
        '--reset-dns' {
            Write-Host 'Reverting Private DNS to default (opportunistic)...'
            & $py -I $tvPy 'settings put global private_dns_mode opportunistic'
            & $py -I $tvPy 'settings get global private_dns_mode'
            exit 0
        }
    }
}

$listPath = if ($ArgsList.Count -ge 1 -and $ArgsList[0]) { (Resolve-Path $ArgsList[0]).Path } else { Join-Path $root 'debloat.txt' }
$stockPkg = if ($ArgsList.Count -ge 2 -and $ArgsList[1]) { $ArgsList[1] } else { 'com.vstv.mercurytransit' }

Write-Host "Restoring hi352l TV at $tvHost using $listPath ..."
& powershell -ExecutionPolicy Bypass -File $restorePs1 -TvHost $tvHost -Stock $stockPkg $listPath
Write-Host 'Restore complete.'
