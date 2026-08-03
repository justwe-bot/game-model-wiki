$ErrorActionPreference = 'Stop'

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw "C# compiler not found: $compiler"
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$output = Join-Path $root 'bin\Hades2GrannyExport.exe'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null

& $compiler /nologo /optimize+ /platform:x64 /target:exe /out:$output (Join-Path $root 'Program.cs')
if ($LASTEXITCODE -ne 0) {
    throw "C# compilation failed with exit code $LASTEXITCODE"
}

Write-Output $output
