$ErrorActionPreference = "Stop"
$logPath = $null

function Write-LauncherError {
    param([Parameter(Mandatory = $true)][string] $Message)

    [Console]::Error.WriteLine($Message)
    if ($null -ne $script:logPath) {
        try {
            [System.IO.File]::AppendAllText($script:logPath, $Message + [Environment]::NewLine)
        }
        catch {
            # The actionable error has already been sent to stderr.
        }
    }
}

if ([string]::IsNullOrWhiteSpace($env:PLUGIN_DATA)) {
    Write-LauncherError "ThreadRecap cannot start: PLUGIN_DATA is missing or empty. Run this hook through Codex plugin loading."
    exit 2
}

try {
    New-Item -ItemType Directory -Path $env:PLUGIN_DATA -Force -ErrorAction Stop | Out-Null
    if (-not (Test-Path -LiteralPath $env:PLUGIN_DATA -PathType Container)) {
        throw "PLUGIN_DATA is not a directory"
    }
    $logPath = Join-Path -Path $env:PLUGIN_DATA -ChildPath "hook.log"
}
catch {
    $logPath = $null
    Write-LauncherError "ThreadRecap cannot start: PLUGIN_DATA cannot be created or is not writable: $env:PLUGIN_DATA"
    exit 3
}

if ([string]::IsNullOrWhiteSpace($env:PLUGIN_ROOT)) {
    Write-LauncherError "ThreadRecap cannot start: PLUGIN_ROOT is missing or empty. Run this hook through Codex plugin loading."
    exit 2
}

if (-not (Test-Path -LiteralPath $env:PLUGIN_ROOT -PathType Container)) {
    Write-LauncherError "ThreadRecap cannot start: PLUGIN_ROOT is not a directory: $env:PLUGIN_ROOT"
    exit 4
}

$entryPoint = Join-Path -Path $env:PLUGIN_ROOT -ChildPath "scripts/hook_entry.py"
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    Write-LauncherError "ThreadRecap cannot start: hook entry point is missing: $entryPoint"
    exit 4
}

$candidates = @(
    @{ Name = "py -3"; Command = "py"; Prefix = @("-3") },
    @{ Name = "python3"; Command = "python3"; Prefix = @() },
    @{ Name = "python"; Command = "python"; Prefix = @() }
)
$selectedCommand = $null
$selectedPrefix = @()

foreach ($candidate in $candidates) {
    if ($null -eq (Get-Command -Name $candidate.Command -ErrorAction SilentlyContinue)) {
        continue
    }

    $command = $candidate.Command
    $prefix = $candidate.Prefix
    & $command @prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
        $selectedCommand = $command
        $selectedPrefix = $prefix
        break
    }
}

if ($null -eq $selectedCommand) {
    Write-LauncherError "ThreadRecap requires Python 3.10 or newer. Install it and ensure py -3, python3, or python is on PATH."
    exit 5
}

& $selectedCommand @selectedPrefix $entryPoint
$status = $LASTEXITCODE
if ($status -ne 0) {
    Write-LauncherError "ThreadRecap hook_entry.py exited with status $status."
}
exit $status
