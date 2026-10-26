# Windows PowerShell script for CleanYourTV Backup, Batched Debloat, Launcher Build & Install,
# Theme Push, and Verification — pre-configured for TV_HOST=172.30.102.100 (model hi352l).
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ArgsList
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$root = $PSScriptRoot
$cyt = Join-Path $root 'cleanyourtv'
if (-not $env:TV_HOST) { $env:TV_HOST = '172.30.102.100' }
$tvHost = $env:TV_HOST

$mode = 'all'
$listPath = Join-Path $root 'debloat.txt'

for ($i = 0; $i -lt $ArgsList.Count; $i++) {
    $a = $ArgsList[$i]
    switch ($a) {
        '--help' {
            Write-Host 'Usage:'
            Write-Host '  build-and-install.bat                          Full backup + debloat + launcher build & install'
            Write-Host '  build-and-install.bat --debloat-only           Backup + batched debloat + screenshots only'
            Write-Host '  build-and-install.bat --launcher-only          Build & install Apple TV 4K launcher only'
            Write-Host '  build-and-install.bat --enable-dns             Enable AdGuard Private DNS (dns.adguard-dns.com)'
            Write-Host '  build-and-install.bat --verify-closed          Verify port 5555 is closed on the TV'
            exit 0
        }
        '-h' {
            Write-Host 'Run build-and-install.bat --help for usage.'
            exit 0
        }
        '--debloat-only' { $mode = 'debloat' }
        '--launcher-only' { $mode = 'launcher' }
        '--enable-dns' { $mode = 'dns' }
        '--verify-closed' { $mode = 'verify-closed' }
        default {
            if ($a -and -not $a.StartsWith('-')) {
                $listPath = (Resolve-Path $a).Path
            }
        }
    }
}

if (-not (Test-Path (Join-Path $cyt 'tools\tv.py'))) {
    Write-Host "Cloning https://github.com/Gathin23/cleanyourtv into $cyt ..."
    git clone https://github.com/Gathin23/cleanyourtv "$cyt"
    if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
}

$py = Join-Path $cyt 'tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Setting up Python virtual environment in cleanyourtv\tools\venv ...'
    $venvDir = Join-Path $cyt 'tools\venv'
    $reqFile = Join-Path $cyt 'tools\requirements.txt'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv "$venvDir"
    } else {
        python -m venv "$venvDir"
    }
    if (-not (Test-Path $py)) {
        throw 'Python 3 is required. Install Python 3 from python.org or Microsoft Store and run again.'
    }
    & $py -m pip install -r "$reqFile"
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}

$tvPy = Join-Path $cyt 'tools\tv.py'
$disablePs1 = Join-Path $cyt 'tools\disable.ps1'
$themePs1 = Join-Path $cyt 'tools\theme.ps1'
$recoveryPy = Join-Path $cyt 'tools\recovery.py'
$adbClosedPy = Join-Path $cyt 'tools\adb-closed.py'
$launcherPs1 = Join-Path $cyt 'launcher\build-and-install.ps1'

if ($mode -eq 'dns') {
    Write-Host "Setting Private DNS to dns.adguard-dns.com on $tvHost..."
    & $py -I $tvPy 'settings put global private_dns_specifier dns.adguard-dns.com'
    & $py -I $tvPy 'settings put global private_dns_mode hostname'
    & $py -I $tvPy 'settings get global private_dns_mode; settings get global private_dns_specifier'
    Write-Host 'To undo: restore.bat --reset-dns'
    exit 0
}

if ($mode -eq 'verify-closed') {
    Write-Host "Checking that network ADB is closed on $tvHost..."
    & $py -I $adbClosedPy $tvHost
    exit $LASTEXITCODE
}

$backupDir = Join-Path $root 'backup'
$shotsDir = Join-Path $root 'shots'
$workDir = Join-Path $root 'work'
New-Item -ItemType Directory -Force -Path $backupDir, $shotsDir, $workDir | Out-Null

Write-Host '======================================================================'
Write-Host "Step 1: Connecting to hi352l TV at $tvHost"
Write-Host "Look at your TV screen now: if 'Allow USB debugging?' appears,"
Write-Host "tick 'Always allow from this computer' and select Allow."
Write-Host '======================================================================'
& $py -I $tvPy --probe
if ($LASTEXITCODE -ne 0) { throw "Could not connect to TV at $tvHost" }

Write-Host ''
Write-Host '======================================================================'
Write-Host 'Step 2: Backing up packages, properties, settings & display state'
Write-Host '======================================================================'
& $py -I $tvPy 'pm list packages -f' | Out-File -Encoding utf8 (Join-Path $backupDir 'packages-all.txt')
& $py -I $tvPy 'pm list packages -d --user 0' | Out-File -Encoding utf8 (Join-Path $backupDir 'packages-disabled.txt')
& $py -I $tvPy 'pm list packages -e --user 0' | Out-File -Encoding utf8 (Join-Path $backupDir 'packages-enabled.txt')
& $py -I $tvPy 'getprop' | Out-File -Encoding utf8 (Join-Path $backupDir 'getprop.txt')
& $py -I $tvPy 'settings list system' | Out-File -Encoding utf8 (Join-Path $backupDir 'settings-system.txt')
& $py -I $tvPy 'settings list secure' | Out-File -Encoding utf8 (Join-Path $backupDir 'settings-secure.txt')
& $py -I $tvPy 'settings list global' | Out-File -Encoding utf8 (Join-Path $backupDir 'settings-global.txt')
& $py -I $tvPy 'wm size; wm density; settings get secure display_density_forced' | Out-File -Encoding utf8 (Join-Path $backupDir 'display.txt')
Write-Host "Saved full backup into $backupDir"

Write-Host 'Capturing initial screenshot -> shots\00-before.png ...'
$ErrorActionPreference = 'Continue'
& $py -I $tvPy --screenshot (Join-Path $shotsDir '00-before.png')
$ErrorActionPreference = 'Stop'

if ($mode -eq 'all' -or $mode -eq 'debloat') {
    Write-Host ''
    Write-Host '======================================================================'
    Write-Host "Step 3: Debloating hi352l in small batches with safety checks & screenshots"
    Write-Host "Using list: $listPath"
    Write-Host '======================================================================'
    & $py -I $recoveryPy --save $listPath

    $pkgs = @(Get-Content $listPath | ForEach-Object { ($_ -replace '#.*$', '').Trim() } | Where-Object { $_ } | ForEach-Object { ($_ -split '\s+')[0] })
    $batchDir = Join-Path $workDir 'batches'
    if (Test-Path $batchDir) { Remove-Item -Recurse -Force $batchDir }
    New-Item -ItemType Directory -Force -Path $batchDir | Out-Null

    $batchSize = 8
    $batchNum = 0
    for ($idx = 0; $idx -lt $pkgs.Count; $idx += $batchSize) {
        $batchNum++
        $end = [Math]::Min($idx + $batchSize - 1, $pkgs.Count - 1)
        $chunk = $pkgs[$idx..$end]
        $bfile = Join-Path $batchDir ("batch_{0:D2}.txt" -f $batchNum)
        $chunk | Out-File -Encoding ascii $bfile
        Write-Host ''
        Write-Host "--- Running Batch $batchNum ---"
        $chunk | ForEach-Object { Write-Host "  $_" }
        & powershell -ExecutionPolicy Bypass -File $disablePs1 -TvHost $tvHost $bfile
        $shotFile = Join-Path $shotsDir ("{0:D2}-after-batch-{0}.png" -f $batchNum)
        Write-Host "Capturing verification screenshot -> $shotFile ..."
        $ErrorActionPreference = 'Continue'
        & $py -I $tvPy --screenshot $shotFile
        $ErrorActionPreference = 'Stop'
    }

    & powershell -ExecutionPolicy Bypass -File $disablePs1 -TvHost $tvHost $listPath
    Write-Host 'Debloat complete!'
}

if ($mode -eq 'all' -or $mode -eq 'launcher') {
    Write-Host ''
    Write-Host '======================================================================'
    Write-Host 'Step 4: Preparing JDK 17 & Android SDK 34 and building Apple TV 4K Launcher'
    Write-Host '======================================================================'

    $toolchainDir = Join-Path $workDir 'toolchain'
    New-Item -ItemType Directory -Force -Path $toolchainDir | Out-Null

    # 1. Ensure JDK 17
    if (-not $env:JAVA_HOME -or -not (Test-Path "$env:JAVA_HOME\bin\java.exe")) {
        $jbr = Get-ChildItem 'C:\Program Files\Android' -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName 'jbr' } | Where-Object { Test-Path "$_\bin\java.exe" } | Select-Object -First 1
        if ($jbr) {
            $env:JAVA_HOME = $jbr
        } else {
            $portableJdkRoot = Join-Path $toolchainDir 'jdk17'
            $existingJava = Get-ChildItem $portableJdkRoot -Recurse -Filter 'java.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $existingJava) {
                Write-Host 'Downloading portable Eclipse Temurin OpenJDK 17 for Windows x64...'
                $jdkZip = Join-Path $toolchainDir 'jdk17.zip'
                Invoke-WebRequest -Uri 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse' -OutFile $jdkZip
                Expand-Archive -Path $jdkZip -DestinationPath $portableJdkRoot -Force
                Remove-Item -Force $jdkZip
                $existingJava = Get-ChildItem $portableJdkRoot -Recurse -Filter 'java.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
            }
            if (-not $existingJava) { throw 'Failed to locate java.exe after extracting OpenJDK 17.' }
            $env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $existingJava.FullName)
        }
    }
    $env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
    Write-Host "Using JAVA_HOME: $env:JAVA_HOME"

    # 2. Ensure Android SDK (platforms;android-34 and build-tools;34.0.0)
    $defaultSdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { "$env:LOCALAPPDATA\Android\Sdk" }
    if (Test-Path (Join-Path $defaultSdk 'platforms\android-34')) {
        $env:ANDROID_HOME = $defaultSdk
    } else {
        $portableSdk = Join-Path $toolchainDir 'android-sdk'
        $sdkManagerBat = Join-Path $portableSdk 'cmdline-tools\latest\bin\sdkmanager.bat'
        if (-not (Test-Path $sdkManagerBat)) {
            Write-Host 'Downloading Android Command-Line Tools for Windows...'
            $cmdZip = Join-Path $toolchainDir 'cmdline-tools.zip'
            Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip' -OutFile $cmdZip
            $extractTmp = Join-Path $toolchainDir 'cmdline-tmp'
            if (Test-Path $extractTmp) { Remove-Item -Recurse -Force $extractTmp }
            Expand-Archive -Path $cmdZip -DestinationPath $extractTmp -Force
            Remove-Item -Force $cmdZip
            $latestDir = Join-Path $portableSdk 'cmdline-tools\latest'
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $latestDir) | Out-Null
            if (Test-Path $latestDir) { Remove-Item -Recurse -Force $latestDir }
            Move-Item -Path (Join-Path $extractTmp 'cmdline-tools') -Destination $latestDir
            Remove-Item -Recurse -Force $extractTmp
        }
        if (-not (Test-Path (Join-Path $portableSdk 'platforms\android-34'))) {
            Write-Host 'Installing Android SDK Platform 34 & Build-Tools 34.0.0...'
            $yesInput = ("y`n" * 30)
            $yesInput | & $sdkManagerBat --sdk_root="$portableSdk" --licenses | Out-Null
            $yesInput | & $sdkManagerBat --sdk_root="$portableSdk" "platforms;android-34" "build-tools;34.0.0" "platform-tools"
        }
        $env:ANDROID_HOME = $portableSdk
    }
    Write-Host "Using ANDROID_HOME: $env:ANDROID_HOME"

    $localProps = Join-Path $cyt 'launcher\local.properties'
    "sdk.dir=$($env:ANDROID_HOME -replace '\\', '/')" | Out-File -Encoding ascii $localProps

    $ErrorActionPreference = 'Continue'
    $enabledNow = (& $py -I $tvPy 'pm list packages -e --user 0' 2>$null) -join "`n"
    $ErrorActionPreference = 'Stop'

    $stockArg = @()
    if ($enabledNow -match 'com\.cvte\.') {
        Write-Host 'Detected CVTE firmware on HiSilicon hi352l: keeping stock launcher enabled (-StockLauncher none).'
        $stockArg = @('-StockLauncher', 'none')
        # Add COMMON_HOME intent category so the CVTE remote Home button recognizes TV Home (#1201)
        $manifestPath = Join-Path $cyt 'launcher\app\src\main\AndroidManifest.xml'
        $manifestContent = Get-Content -Raw $manifestPath
        if ($manifestContent -notmatch 'android\.intent\.category\.COMMON_HOME') {
            Write-Host 'Adding android.intent.category.COMMON_HOME to MainActivity for CVTE Home button support...'
            $manifestContent = $manifestContent -replace '(<category android:name="android\.intent\.category\.HOME"\s*/>)', "`$1`r`n                <category android:name=`"android.intent.category.COMMON_HOME`" />"
            Set-Content -Path $manifestPath -Value $manifestContent -Encoding utf8
        }
    } elseif ($enabledNow -match 'package:com\.zeasn\.whale\.saas') {
        $stockArg = @('-StockLauncher', 'com.zeasn.whale.saas')
    } elseif ($enabledNow -match 'package:com\.bestv\.ott') {
        $stockArg = @('-StockLauncher', 'com.bestv.ott')
    }

    & powershell -ExecutionPolicy Bypass -File $launcherPs1 -TvHost $tvHost @stockArg
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build-and-install failed' }

    $myTheme = Join-Path $root 'my-theme'
    if (Test-Path (Join-Path $myTheme 'config.json')) {
        Write-Host 'Applying custom dock/layout configuration from my-theme\config.json ...'
        & powershell -ExecutionPolicy Bypass -File $themePs1 -TvHost $tvHost $myTheme
    }

    Write-Host 'Capturing Home & D-pad verification screenshots...'
    $ErrorActionPreference = 'Continue'
    & $py -I $tvPy --screenshot (Join-Path $shotsDir '10-launcher-home.png') HOME
    & $py -I $tvPy --screenshot (Join-Path $shotsDir '11-dpad-right.png') DPAD_RIGHT
    & $py -I $tvPy --screenshot (Join-Path $shotsDir '12-dpad-up-topbar.png') DPAD_UP
    & $py -I $tvPy --screenshot (Join-Path $shotsDir '13-dpad-down-dock.png') DPAD_DOWN
    $ErrorActionPreference = 'Stop'
}

Write-Host ''
Write-Host '======================================================================'
Write-Host "All steps finished! Backups are in $backupDir and screenshots in $shotsDir."
Write-Host 'When finished testing, turn OFF ADB on the TV, reboot, and run:'
Write-Host '  build-and-install.bat --verify-closed'
Write-Host '======================================================================'
