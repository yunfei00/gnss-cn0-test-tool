param([string]$Python = "python", [string]$Iscc)

$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & $Python -c "import struct; assert struct.calcsize('P') == 8"
    if ($LASTEXITCODE -ne 0) { throw "Use x64 Python" }
    $bundle = @(
        "--clean", "--noconfirm", "--windowed", "--name", "GNSS-CN0",
        "--paths", "src", "--hidden-import", "gnss_cn0_tool.gui", "src/gnss_cn0_tool/cli.py"
    )
    & $Python -m PyInstaller @bundle
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:LOCALAPPDATA = Join-Path $PWD "build/smoke"
    $app = Resolve-Path "dist/GNSS-CN0/GNSS-CN0.exe"
    $smoke = Start-Process -FilePath $app -ArgumentList "--smoke" -Wait -PassThru -WindowStyle Hidden
    if ($smoke.ExitCode -ne 0) { throw "Frozen application failed to start" }
    Compress-Archive -LiteralPath "dist/GNSS-CN0" -DestinationPath "dist/GNSS-CN0-Portable-x64.zip" -Force
    if ($Iscc) {
        & $Iscc "packaging/windows.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    }
    Get-ChildItem dist/GNSS-CN0-* -File | Where-Object Extension -ne ".sha256" | ForEach-Object {
        $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$digest  $($_.Name)" | Set-Content -Encoding ascii -LiteralPath "$($_.FullName).sha256"
    }
} finally {
    Pop-Location
}
