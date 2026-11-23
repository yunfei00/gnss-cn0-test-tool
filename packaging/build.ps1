param(
    [string]$Python = "python",
    [string]$Iscc,
    [string]$SignTool,
    [string]$SigningThumbprint,
    [string]$TimestampUrl
)

$ErrorActionPreference = "Stop"
if (($SignTool -or $SigningThumbprint -or $TimestampUrl) -and
    -not ($SignTool -and $SigningThumbprint -match '^[a-fA-F0-9]{40}$' -and $TimestampUrl)) {
    throw "Specify SignTool, a certificate store thumbprint, and the timestamp URL together."
}
function Invoke-ReleaseSigning([string]$File) {
    if (-not $SignTool) { return }
    & $SignTool sign /sha1 $SigningThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $File
    if ($LASTEXITCODE -ne 0) { throw "Signing failed: $File" }
    & $SignTool verify /pa $File
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $File" }
}
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & $Python -c "import struct; assert struct.calcsize('P') == 8"
    if ($LASTEXITCODE -ne 0) { throw "Use x64 Python" }
    $appVersion = & $Python -c "from importlib.metadata import version; print(version('gnss-cn0-test-tool'))"
    if ($LASTEXITCODE -ne 0 -or $appVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Install project metadata with uv sync first." }
    $bundle = @(
        "--clean", "--noconfirm", "--windowed", "--name", "GNSS-CN0",
        "--paths", "src", "--hidden-import", "gnss_cn0_tool.gui",
        "--copy-metadata", "gnss-cn0-test-tool", "src/gnss_cn0_tool/cli.py"
    )
    & $Python -m PyInstaller @bundle
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:LOCALAPPDATA = Join-Path $PWD "build/smoke"
    $app = Resolve-Path "dist/GNSS-CN0/GNSS-CN0.exe"
    Invoke-ReleaseSigning $app.Path
    $smoke = Start-Process -FilePath $app -ArgumentList "--smoke" -Wait -PassThru -WindowStyle Hidden
    if ($smoke.ExitCode -ne 0) { throw "Frozen application failed to start" }
    Compress-Archive -LiteralPath "dist/GNSS-CN0" -DestinationPath "dist/GNSS-CN0-Portable-x64.zip" -Force
    if ($Iscc) {
        & $Iscc "/DAppVersion=$appVersion" "packaging/windows.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Invoke-ReleaseSigning (Resolve-Path "dist/GNSS-CN0-Setup-x64.exe").Path
    }
    Get-ChildItem dist/GNSS-CN0-* -File | Where-Object Extension -ne ".sha256" | ForEach-Object {
        $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$digest  $($_.Name)" | Set-Content -Encoding ascii -LiteralPath "$($_.FullName).sha256"
    }
} finally {
    Pop-Location
}
