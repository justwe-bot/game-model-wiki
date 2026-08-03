param(
    [Parameter(Mandatory = $true)]
    [string]$Asset,

    [Parameter(Mandatory = $true)]
    [string]$ModelEntry,

    [Parameter(Mandatory = $true)]
    [string]$OutputSlug,

    [ValidateRange(0.1, 1.0)]
    [double]$LowRatio = 0.7,

    [string]$GameRoot = 'C:\Program Files (x86)\Steam\steamapps\common\Hades II',

    [string]$BlenderPath = $env:HADES2_BLENDER
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$optimizedRoot = Join-Path $GameRoot 'Content\GR2\_Optimized'
$grannyDll = Join-Path $GameRoot 'Release\granny2_x64.dll'
$gpkPath = Join-Path $optimizedRoot "$Asset.gpk"
$sdbPath = Join-Path $optimizedRoot "$Asset.sdb"
$rebuildTool = Join-Path $repoRoot 'tools\hades2-granny-rebuild\bin\Hades2GrannyRebuild.exe'
$exportTool = Join-Path $repoRoot 'tools\hades2-granny-export\bin\Hades2GrannyExport.exe'
$inspectScript = Join-Path $repoRoot 'scripts\extract_hades2_gpk.py'
$blenderScript = Join-Path $repoRoot 'scripts\build_hades2_glb.py'

if (-not $BlenderPath) {
    $BlenderPath = Join-Path $env:TEMP 'codex-hades2-model-pipeline\blender-4.2.9-windows-x64\blender.exe'
}

foreach ($requiredPath in @($grannyDll, $gpkPath, $sdbPath, $inspectScript, $blenderScript, $BlenderPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path does not exist: $requiredPath"
    }
}

if (-not (Test-Path -LiteralPath $rebuildTool)) {
    & (Join-Path $repoRoot 'tools\hades2-granny-rebuild\build.ps1')
}
if (-not (Test-Path -LiteralPath $exportTool)) {
    & (Join-Path $repoRoot 'tools\hades2-granny-export\build.ps1')
}

$buildRoot = Join-Path $env:TEMP "codex-hades2-assets\$Asset"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

$entryLines = & python $inspectScript $gpkPath
$entries = @(
    $entryLines | ForEach-Object {
        if ($_ -match '^([^:]+):') { $Matches[1] }
    }
)

if ($entries -notcontains $ModelEntry) {
    throw "Model entry '$ModelEntry' was not found in $gpkPath"
}

foreach ($entry in $entries) {
    $gr2Path = Join-Path $buildRoot "$entry.gr2"
    & $rebuildTool $grannyDll $sdbPath $gpkPath $entry $gr2Path
    if ($LASTEXITCODE -ne 0) {
        throw "GR2 reconstruction failed for $entry"
    }
}

$meshPath = Join-Path $buildRoot "$ModelEntry.gr2"
$animationPaths = @(
    $entries |
        Where-Object { $_ -ne $ModelEntry } |
        Sort-Object |
        ForEach-Object { Join-Path $buildRoot "$_.gr2" }
)
$bundlePath = Join-Path $buildRoot "$Asset.h2gx"

& $exportTool $grannyDll $meshPath $bundlePath @animationPaths
if ($LASTEXITCODE -ne 0) {
    throw "H2GX export failed for $Asset"
}

$originalPath = Join-Path $repoRoot "models\$OutputSlug-original.glb"
$lowPath = Join-Path $repoRoot "models\$OutputSlug-low.glb"
& $BlenderPath --background --python $blenderScript -- $bundlePath $originalPath $lowPath --low-ratio $LowRatio
if ($LASTEXITCODE -ne 0) {
    throw "Blender GLB build failed for $Asset"
}

Get-Item -LiteralPath $originalPath, $lowPath |
    Select-Object Name, Length, LastWriteTime
