Param(
    [string]$BuildType = "Release"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceDir = Join-Path $ProjectRoot "src/native/cpp"
$BuildDir = Join-Path $ProjectRoot "build/native"

if (!(Test-Path $SourceDir)) {
    throw "Native source directory not found: $SourceDir"
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

cmake -S $SourceDir -B $BuildDir -DCMAKE_BUILD_TYPE=$BuildType
cmake --build $BuildDir --config $BuildType

Write-Host "Native build finished: $BuildDir"
