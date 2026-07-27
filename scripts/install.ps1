$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$InstallerUrl = "https://raw.githubusercontent.com/GotoLu/cx-notifier-marketplace/main/scripts/install.py"
$InstallerPath = Join-Path ([System.IO.Path]::GetTempPath()) "cx-notifier-install.py"

function Resolve-Python {
    $candidates = @(
        @{ Command = "py"; Prefix = @("-3") },
        @{ Command = "python"; Prefix = @() },
        @{ Command = "python3"; Prefix = @() }
    )

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        $commandPath = $command.Source
        if ($commandPath -like "*\WindowsApps\*") {
            continue
        }
        $commandPrefix = @($candidate.Prefix)
        & $commandPath @commandPrefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{
                Command = $commandPath
                Prefix = $commandPrefix
            }
        }
    }

    throw "CX Notifier requires Python 3.10 or newer. Install it with: winget install -e --id Python.Python.3.12"
}

$python = Resolve-Python
$pythonCommand = $python.Command
$pythonPrefix = @($python.Prefix)
$installerArguments = @("--channel", "feishu")
if ($args.Count -gt 0) {
    $installerArguments = @($args)
}
$hadPythonUtf8 = Test-Path Env:PYTHONUTF8
$previousPythonUtf8 = $env:PYTHONUTF8

try {
    $env:PYTHONUTF8 = "1"
    Write-Host "Downloading the CX Notifier installer..."
    Invoke-WebRequest -UseBasicParsing -Uri $InstallerUrl -OutFile $InstallerPath
    & $pythonCommand @pythonPrefix $InstallerPath @installerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "CX Notifier installation failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
    if ($hadPythonUtf8) {
        $env:PYTHONUTF8 = $previousPythonUtf8
    }
    else {
        Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    }
}
