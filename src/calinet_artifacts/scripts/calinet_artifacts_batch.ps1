param(
    [string]$InputDir = "",

    [ValidateSet("scr","ecg","eye1","eye2","resp")]
    [string]$Modality = "scr",

    [string]$ArtifactsExe = "calinet-artifacts",
    [string]$PythonExe = "python",
    [string]$OutputDir = "",

    [switch]$Pspm,
    [switch]$DebugMode,
    [Alias("Overwrite")][switch]$Force,
    [switch]$Help
)

function Quote-Arg([string]$s) {
    if ($null -eq $s) { return '""' }
    return '"' + ($s -replace '"', '""') + '"'
}

function Resolve-OptionalPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) { return "" }
    if (-not (Test-Path $PathValue)) {
        Write-Host "ERROR: Path not found: $PathValue"
        exit 1
    }
    return (Resolve-Path $PathValue).Path
}

function Get-ProjectRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StartPath
    )

    $current = $StartPath
    if (Test-Path $current -PathType Leaf) {
        $current = Split-Path $current -Parent
    }

    $current = (Resolve-Path $current).Path

    while ($true) {
        $hasDerivatives = Test-Path (Join-Path $current "derivatives")
        $hasSubjects = @(Get-ChildItem -Path $current -Directory -Filter "sub-*" -ErrorAction SilentlyContinue).Count -gt 0

        if ($hasDerivatives -or $hasSubjects) {
            return $current
        }

        $parent = Split-Path $current -Parent
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            return $StartPath
        }

        $current = $parent
    }
}


function Get-TaskNameFromPhysioFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PhysioFile
    )

    $name = [System.IO.Path]::GetFileName($PhysioFile)
    $match = [regex]::Match($name, "_task-([^_]+)")
    if ($match.Success) {
        return $match.Groups[1].Value
    }

    return "<unknown-task>"
}

function Get-SubjectNameFromPhysioFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PhysioFile
    )

    $current = Split-Path $PhysioFile -Parent
    while ($current) {
        $leaf = Split-Path $current -Leaf
        if ($leaf -like "sub-*") {
            return $leaf
        }

        $parent = Split-Path $current -Parent
        if ($parent -eq $current) {
            break
        }
        $current = $parent
    }

    return "<unknown-subject>"
}

function Get-DerivativePathFromPython([string]$PhysioFile, [string]$ResolvedProjectRoot, [string]$ResolvedOutputDir, [bool]$UsePspm) {
    $effectiveOutputDir = if ([string]::IsNullOrWhiteSpace($ResolvedOutputDir)) {
        Join-Path $ResolvedProjectRoot "derivatives"
    } else {
        $ResolvedOutputDir
    }

    $pySnippet = @'
import json
import os
import sys

try:
    from calinet_artifacts.gui import build_derivative_paths
except Exception as exc:
    print(json.dumps({"ok": False, "reason": f"import failed: {exc}"}))
    raise SystemExit(0)

physio_file = sys.argv[1]
output_dir = sys.argv[2] or None
force_pspm = sys.argv[3] == "1"

call_variants = [
    {"file": physio_file, "output_dir": output_dir, "force_pspm": force_pspm},
    {"physio_file": physio_file, "output_dir": output_dir, "force_pspm": force_pspm},
    {"input_file": physio_file, "output_dir": output_dir, "force_pspm": force_pspm},
    {"file": physio_file, "output_dir": output_dir},
    {"physio_file": physio_file, "output_dir": output_dir},
    {"input_file": physio_file, "output_dir": output_dir},
    {"physio_file": physio_file},
    {"file": physio_file},
]

for kwargs in call_variants:
    try:
        out = build_derivative_paths(**kwargs)
        if isinstance(out, (tuple, list)) and out:
            out = out[0]
        if out is not None:
            print(json.dumps({"ok": True, "path": os.fspath(out)}))
            raise SystemExit(0)
    except TypeError:
        continue
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        raise SystemExit(0)

print(json.dumps({"ok": False, "reason": "No compatible build_derivative_paths signature found"}))
'@

    $result = & $PythonExe -c $pySnippet $PhysioFile $effectiveOutputDir ($(if ($UsePspm) { "1" } else { "0" })) 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($result)) {
        return $null
    }

    try {
        $parsed = $result | ConvertFrom-Json
    } catch {
        return $null
    }

    if ($parsed.ok -and -not [string]::IsNullOrWhiteSpace($parsed.path)) {
        return [string]$parsed.path
    }

    return $null
}

function Find-DerivativeBySearch([string]$PhysioFile, [string]$ResolvedProjectRoot, [string]$ResolvedOutputDir, [bool]$UsePspm) {
    $root = if ([string]::IsNullOrWhiteSpace($ResolvedOutputDir)) {
        Join-Path $ResolvedProjectRoot "derivatives"
    } else {
        $ResolvedOutputDir
    }

    if (-not (Test-Path $root)) { return $null }

    $base = [System.IO.Path]::GetFileNameWithoutExtension($PhysioFile)
    if ($base.EndsWith(".tsv", [System.StringComparison]::OrdinalIgnoreCase)) {
        $base = [System.IO.Path]::GetFileNameWithoutExtension($base)
    }

    $prefix = $base -replace '_physio$',''

    $patterns = @()
    if ($UsePspm) {
        $patterns += "$prefix*_desc-artifacts*.mat"
        $patterns += "$prefix*.mat"
    } else {
        $patterns += "$prefix*_desc-artifacts_physioevents.tsv.gz"
        $patterns += "$prefix*_desc-artifacts_physioevents.tsv"
        $patterns += "$prefix*_desc-artifacts_physioevents.json"
        $patterns += "$prefix*physioevents.tsv.gz"
        $patterns += "$prefix*physioevents.tsv"
        $patterns += "$prefix*physioevents.json"
    }

    foreach ($pattern in $patterns) {
        $match = Get-ChildItem -Path $root -Filter $pattern -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            Select-Object -First 1
        if ($null -ne $match) {
            return $match.FullName
        }
    }

    return $null
}

function Test-ArtifactExists([string]$PhysioFile, [string]$ResolvedProjectRoot, [string]$ResolvedOutputDir, [bool]$UsePspm) {
    $derivedPath = Get-DerivativePathFromPython -PhysioFile $PhysioFile -ResolvedProjectRoot $ResolvedProjectRoot -ResolvedOutputDir $ResolvedOutputDir -UsePspm $UsePspm
    if (-not [string]::IsNullOrWhiteSpace($derivedPath) -and (Test-Path $derivedPath)) {
        return [PSCustomObject]@{
            Exists = $true
            Path = $derivedPath
            Method = "build_derivative_paths"
        }
    }

    $fallbackPath = Find-DerivativeBySearch -PhysioFile $PhysioFile -ResolvedProjectRoot $ResolvedProjectRoot -ResolvedOutputDir $ResolvedOutputDir -UsePspm $UsePspm
    if (-not [string]::IsNullOrWhiteSpace($fallbackPath)) {
        return [PSCustomObject]@{
            Exists = $true
            Path = $fallbackPath
            Method = "search"
        }
    }

    return [PSCustomObject]@{
        Exists = $false
        Path = ""
        Method = ""
    }
}

if ($Help) {
    Write-Host ""
    Write-Host "CALINET artifacts batch runner"
    Write-Host "==============================================================="
    Write-Host ""
    Write-Host "USAGE"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir <dir> [options]"
    Write-Host ""
    Write-Host "REQUIRED"
    Write-Host "  -InputDir        Dataset root, subject folder, or physio folder"
    Write-Host ""
    Write-Host "OPTIONAL"
    Write-Host "  -Modality        scr, ecg, resp, eye1 or eye2 (default: scr)"
    Write-Host "  -ArtifactsExe    CLI executable or command name (default: calinet-artifacts)"
    Write-Host "  -PythonExe       Python executable used to probe build_derivative_paths"
    Write-Host "  -OutputDir       Derivatives/output directory override"
    Write-Host "  -Pspm            Launch GUI with --pspm"
    Write-Host "  -Force           Open GUI even if an artifact derivative already exists"
    Write-Host "  -Overwrite       Alias for -Force"
    Write-Host "  -DebugMode       Print commands before running them"
    Write-Host ""
    Write-Host "EXAMPLES"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir 'Z:\CALINET2\converted\amsterdam'"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir 'Z:\CALINET2\converted\amsterdam\sub-CalinetAmsterdam01'"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir 'Z:\CALINET2\converted\amsterdam\sub-CalinetAmsterdam01\physio'"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir 'Z:\CALINET2\converted\amsterdam' -Modality ecg"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir 'Z:\CALINET2\converted\amsterdam' -Modality scr -Pspm"
    Write-Host "  .\calinet_artifacts_batch.ps1 -InputDir 'Z:\CALINET2\converted\amsterdam' -OutputDir 'Z:\CALINET2\converted\amsterdam\derivatives'"
    Write-Host ""
    exit 0
}

if ([string]::IsNullOrWhiteSpace($InputDir)) {
    Write-Host "ERROR: InputDir is required"
    exit 1
}

if (-not (Test-Path $InputDir)) {
    Write-Host "ERROR: InputDir not found: $InputDir"
    exit 1
}

$InputDirResolved = (Resolve-Path $InputDir).Path
$ProjectRootResolved = Get-ProjectRoot -StartPath $InputDirResolved
$OutputDirResolved = Resolve-OptionalPath $OutputDir

$escapedModality = [Regex]::Escape($Modality)

$physioFiles = Get-ChildItem -Path $InputDirResolved -Recurse -File |
    Where-Object {
        $_.Name -match "_recording-$escapedModality`_physio\.tsv\.gz$"
    } |
    Sort-Object FullName

if ($physioFiles.Count -eq 0) {
    Write-Host "ERROR: No matching '$Modality' physio files found below $InputDirResolved"
    exit 1
}

$jobs = @()
foreach ($physioFile in $physioFiles) {
    $artifactStatus = Test-ArtifactExists `
        -PhysioFile $physioFile.FullName `
        -ResolvedProjectRoot $ProjectRootResolved `
        -ResolvedOutputDir $OutputDirResolved `
        -UsePspm $Pspm.IsPresent

    $jobs += [PSCustomObject]@{
        Subject         = Get-SubjectNameFromPhysioFile -PhysioFile $physioFile.FullName
        Task            = Get-TaskNameFromPhysioFile -PhysioFile $physioFile.FullName
        PhysioFile      = $physioFile.FullName
        ArtifactExists  = $artifactStatus.Exists
        ArtifactPath    = $artifactStatus.Path
        DetectionMethod = $artifactStatus.Method
    }
}

$toOpen = @()
$skipped = @()

foreach ($job in $jobs) {
    if ($job.ArtifactExists -and -not $Force) {
        $skipped += $job
    } else {
        $toOpen += $job
    }
}

Write-Host ""
Write-Host "CALINET artifacts batch runner"
Write-Host "==============================================================="
Write-Host "InputDir    : $InputDirResolved"
Write-Host "ProjectRoot : $ProjectRootResolved"
Write-Host "Modality    : $Modality"
Write-Host "OutputDir   : $(if ([string]::IsNullOrWhiteSpace($OutputDirResolved)) { '<default>' } else { $OutputDirResolved })"
Write-Host "PsPM        : $($Pspm.IsPresent)"
Write-Host "Force       : $($Force.IsPresent)"
Write-Host "Found       : $($jobs.Count) matching file(s)"
Write-Host "Skip        : $($skipped.Count) existing derivative(s)"
Write-Host "Open        : $($toOpen.Count) file(s)"
Write-Host ""

foreach ($job in $skipped) {
    Write-Host "SKIP: $($job.Subject)  task=$($job.Task)"
    Write-Host "      Physio    : $($job.PhysioFile)"
    Write-Host "      Derivative: $($job.ArtifactPath) [$($job.DetectionMethod)]"
}

Write-Host "Press q to quit and close the current GUI. Press Ctrl+C to abort immediately."

foreach ($job in $toOpen) {
    $argsList = @("gui", "--file", $job.PhysioFile)

    if (-not [string]::IsNullOrWhiteSpace($OutputDirResolved)) {
        $argsList += @("--output-dir", $OutputDirResolved)
    }

    if ($Pspm) {
        $argsList += "--pspm"
    }

    $action = if ($job.ArtifactExists -and $Force) { "REOPEN" } else { "OPEN" }
    Write-Host "${action}: $($job.Subject)  task=$($job.Task)"
    Write-Host "      $($job.PhysioFile)"
    if ($job.ArtifactExists) {
        Write-Host "      Existing  : $($job.ArtifactPath) [$($job.DetectionMethod)]"
    }

    $proc = Start-Process -FilePath $ArtifactsExe -ArgumentList $argsList -PassThru

    while (-not $proc.HasExited) {
        Start-Sleep -Milliseconds 200
        $proc.Refresh()

        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.KeyChar -eq 'q') {
                    Write-Host "Quit requested. Terminating current GUI and exiting batch."
                    taskkill /PID $proc.Id /T /F | Out-Null
                    return
                }
            }
        } catch {
            # ignore console-read issues in non-interactive hosts
        }
    }
}

exit 0
