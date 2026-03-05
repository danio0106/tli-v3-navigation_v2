Param(
    [string]$BuildType = "Release",
    [string]$Generator = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceDir = Join-Path $ProjectRoot "src/native/cpp"
$BuildDir = Join-Path $ProjectRoot "build/native"
$VenvCmake = Join-Path $ProjectRoot ".venv/Scripts/cmake.exe"
$VenvPython = Join-Path $ProjectRoot ".venv/Scripts/python.exe"
$CmakeExe = if (Test-Path $VenvCmake) { $VenvCmake } else { "cmake" }
$Pybind11Dir = ""

if (-not $Generator -and $IsWindows) {
    # Avoid defaulting to NMake/Ninja in plain shells; VS generator works
    # without launching a Developer Command Prompt.
    $Generator = "Visual Studio 17 2022"
}

if (Test-Path $VenvPython) {
    try {
        $Pybind11Dir = & $VenvPython -m pybind11 --cmakedir
        if ($LASTEXITCODE -ne 0) {
            $Pybind11Dir = ""
        }
    }
    catch {
        $Pybind11Dir = ""
    }
}

if (!(Test-Path $SourceDir)) {
    throw "Native source directory not found: $SourceDir"
}

if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

if ($Generator) {
    $configureArgs = @("-S", $SourceDir, "-B", $BuildDir, "-G", $Generator, "-A", "x64", "-DCMAKE_BUILD_TYPE=$BuildType", "-DPYBIND11_FINDPYTHON=ON")
    if (Test-Path $VenvPython) {
        $configureArgs += "-DPython_EXECUTABLE=$VenvPython"
    }
    if ($Pybind11Dir) {
        $configureArgs += "-Dpybind11_DIR=$Pybind11Dir"
    }
    & $CmakeExe @configureArgs
}
else {
    $configureArgs = @("-S", $SourceDir, "-B", $BuildDir, "-DCMAKE_BUILD_TYPE=$BuildType", "-DPYBIND11_FINDPYTHON=ON")
    if (Test-Path $VenvPython) {
        $configureArgs += "-DPython_EXECUTABLE=$VenvPython"
    }
    if ($Pybind11Dir) {
        $configureArgs += "-Dpybind11_DIR=$Pybind11Dir"
    }
    & $CmakeExe @configureArgs
}
if ($LASTEXITCODE -ne 0) {
    throw "Native configure failed (exit code $LASTEXITCODE)."
}

& $CmakeExe --build $BuildDir --config $BuildType
if ($LASTEXITCODE -ne 0) {
    throw "Native build failed (exit code $LASTEXITCODE)."
}

$ArtifactDir = if ($Generator) { Join-Path $BuildDir $BuildType } else { $BuildDir }
$BuiltPyd = Get-ChildItem -Path $ArtifactDir -Filter "tli_native*.pyd" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $BuiltPyd) {
    throw "Native build completed but no tli_native*.pyd artifact found in $ArtifactDir"
}

$DstDir = Join-Path $ProjectRoot "src/native"
New-Item -ItemType Directory -Force -Path $DstDir | Out-Null
Copy-Item -Path $BuiltPyd.FullName -Destination $DstDir -Force
Write-Host "Copied native module to: $DstDir"

Write-Host "Native build finished: $BuildDir"
