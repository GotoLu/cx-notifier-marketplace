$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Installer = Join-Path $RepositoryRoot "scripts\install.ps1"
$Launcher = Join-Path $RepositoryRoot "plugins\cx-plugin\hooks\notify.cmd"
$TemporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("cx-notifier-windows-" + [System.Guid]::NewGuid().ToString("N"))
$FakeBin = Join-Path $TemporaryRoot "bin"
$CommandLog = Join-Path $TemporaryRoot "codex-commands.log"

$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $Installer,
    [ref]$tokens,
    [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -ne 0) {
    throw "install.ps1 has PowerShell parse errors: $($parseErrors -join '; ')"
}

New-Item -ItemType Directory -Path $FakeBin -Force | Out-Null
$fakeCodex = @'
@echo off
echo %*>>"%CX_WINDOWS_TEST_LOG%"
if "%1 %2 %3"=="plugin list --json" echo []
if "%1 %2 %3"=="plugin marketplace list" echo []
exit /b 0
'@
Set-Content -LiteralPath (Join-Path $FakeBin "codex.cmd") -Value $fakeCodex -Encoding Ascii

$originalPath = $env:PATH
$env:PATH = "$FakeBin;$originalPath"
$env:CX_WINDOWS_TEST_LOG = $CommandLog
$env:CX_NOTIFY_CONFIG = Join-Path $TemporaryRoot "missing-config.json"
$env:CX_NOTIFY_DATA = Join-Path $TemporaryRoot "data"

function Invoke-WebRequest {
    param(
        [switch]$UseBasicParsing,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )

    $null = $UseBasicParsing
    $null = $Uri
    Copy-Item -LiteralPath (Join-Path $RepositoryRoot "scripts\install.py") -Destination $OutFile -Force
}

try {
    & $Installer --channel none
    if ($LASTEXITCODE -ne 0) {
        throw "install.ps1 returned exit code $LASTEXITCODE"
    }

    $commands = Get-Content -LiteralPath $CommandLog
    foreach ($expected in @(
        "plugin list --json",
        "plugin marketplace list",
        "plugin marketplace add GotoLu/cx-notifier-marketplace",
        "plugin add cx-plugin@cx-notifier"
    )) {
        if (-not ($commands -contains $expected)) {
            throw "install.ps1 did not invoke expected Codex command: $expected"
        }
    }

    $hookOutput = '{}' | & cmd.exe /d /c "call `"$Launcher`""
    if ($LASTEXITCODE -ne 0) {
        throw "notify.cmd returned exit code $LASTEXITCODE"
    }
    if (-not (($hookOutput -join "`n").Trim().EndsWith("{}"))) {
        throw "notify.cmd did not return the fail-open JSON response"
    }

    Write-Host "Windows installer and Hook launcher acceptance passed."
}
finally {
    $env:PATH = $originalPath
    Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}
