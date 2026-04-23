# build and deploy the android app to a connected device

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
$AndroidDir = Join-Path $ProjectRoot "android"

# ensure we are in the android directory where gradlew exists
Push-Location $AndroidDir

try {
    Write-Host "Building and installing debug app..." -ForegroundColor Cyan

    # run gradlew installDebug which builds and installs in one step
    # if build fails, the script will exit with the gradle error
    .\gradlew.bat :app:installDebug

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`nInstallation successful!" -ForegroundColor Green

        # attempt to start the app
        # we'll look for adb in common locations if not in path
        $adb = "adb"
        if (!(Get-Command $adb -ErrorAction SilentlyContinue)) {
            $localAppData = [System.Environment]::GetFolderPath('LocalApplicationData')
            $adb = "$localAppData\Android\Sdk\platform-tools\adb.exe"
        }

        if (Test-Path $adb) {
            Write-Host "Starting app: io.github.johnjanthony.switchboard..." -ForegroundColor Cyan
            & $adb shell monkey -p io.github.johnjanthony.switchboard -c android.intent.category.LAUNCHER 1 | Out-Null
        } else {
            Write-Host "Warning: adb not found in PATH or default SDK location. Please start the app manually on your phone." -ForegroundColor Yellow
        }
    } else {
        Write-Error "Gradle build or installation failed."
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
