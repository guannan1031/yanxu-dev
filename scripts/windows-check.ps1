[CmdletBinding()]
param(
    [switch]$RequireAuthTools,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Add-CheckFailure {
    param([string]$Message)
    $failures.Add($Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Add-CheckWarning {
    param([string]$Message)
    $warnings.Add($Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Test-ExternalTool {
    param(
        [string]$Name,
        [switch]$Required
    )

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        if ($Required) {
            Add-CheckFailure "$Name is not available on PATH."
        }
        else {
            Add-CheckWarning "$Name is not available on PATH."
        }
        return $false
    }

    Write-Host "[PASS] $Name found: $($command.Source)" -ForegroundColor Green
    return $true
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    Write-Host "Yanxu Dev Windows self-check"
    Write-Host "Repository: $repoRoot"

    if (-not (Test-ExternalTool -Name "python" -Required)) {
        Add-CheckFailure "Activate the Python 3.11+ virtual environment, then rerun this script."
    }
    else {
        $pythonVersion = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
        if ($LASTEXITCODE -ne 0) {
            Add-CheckFailure "Python could not execute."
        }
        else {
            $versionParts = $pythonVersion.Trim().Split('.')
            $supported = ([int]$versionParts[0] -gt 3) -or (
                ([int]$versionParts[0] -eq 3) -and ([int]$versionParts[1] -ge 11)
            )
            if ($supported) {
                Write-Host "[PASS] Python $pythonVersion is supported." -ForegroundColor Green
            }
            else {
                Add-CheckFailure "Python $pythonVersion is too old; Yanxu Dev requires Python 3.11+."
            }
        }
    }

    if (Test-ExternalTool -Name "git" -Required) {
        $gitRoot = (& git rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -ne 0) {
            Add-CheckFailure "The current directory is not inside a Git checkout."
        }
        else {
            Write-Host "[PASS] Git checkout: $gitRoot" -ForegroundColor Green
        }

        $autoCrlf = (& git config --get core.autocrlf 2>$null)
        if ($LASTEXITCODE -eq 0 -and $autoCrlf.Trim().ToLowerInvariant() -eq "true") {
            Add-CheckWarning "core.autocrlf=true can rewrite LF files. Run: git config --local core.autocrlf false"
        }
        else {
            Write-Host "[PASS] Git is not configured to rewrite this checkout to CRLF." -ForegroundColor Green
        }
    }

    if (Test-Path "pyproject.toml" -PathType Leaf) {
        Write-Host "[PASS] pyproject.toml found." -ForegroundColor Green
    }
    else {
        Add-CheckFailure "pyproject.toml is missing from the repository root."
    }

    if (Get-Command python -CommandType Application -ErrorAction SilentlyContinue) {
        & python -m yanxu --help *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[PASS] Yanxu CLI entry point is runnable." -ForegroundColor Green
        }
        else {
            Add-CheckFailure "python -m yanxu --help failed."
        }

        if (-not $SkipTests) {
            Write-Host "Running the complete test suite..."
            & python -m unittest discover -s tests -v
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[PASS] Complete test suite passed." -ForegroundColor Green
            }
            else {
                Add-CheckFailure "The complete test suite failed."
            }
        }
    }

    $null = Test-ExternalTool -Name "gh" -Required:$RequireAuthTools
    $null = Test-ExternalTool -Name "codex" -Required:$RequireAuthTools

    Write-Host ""
    Write-Host "Result: $($failures.Count) failure(s), $($warnings.Count) warning(s)."
    if ($failures.Count -gt 0) {
        exit 1
    }
    exit 0
}
finally {
    Pop-Location
}
