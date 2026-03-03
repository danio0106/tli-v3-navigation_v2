param(
    [string]$LogPath = "",
    [switch]$Latest
)

$root = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $root "logs"

if (-not $LogPath) {
    $logFile = Get-ChildItem -Path $logsDir -Filter "bot_*.log" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $logFile) {
        Write-Host "No bot_*.log files found in $logsDir"
        exit 1
    }
    $LogPath = $logFile.FullName
}

if (-not (Test-Path $LogPath)) {
    Write-Host "Log file not found: $LogPath"
    exit 1
}

$nearPattern = [regex]'^(?<ts>\d{2}:\d{2}:\d{2}).*Near events — Carjack\([^\)]*\):\d+\(A:(?<A>-?\d+),H:(?<H>-?\d+),'
$linkPattern = [regex]'\[CarjackLink\]'
$linkHitPattern = [regex]'\[CarjackLink\].*(P\+0x|A\+0x)'
$phasePattern = [regex]'\[CarjackPhase\]'
$guardPattern = [regex]'\[GUARD-TGT\]'
$noisePattern = [regex]'read_bytes failed at 0xE00000037'

$maxA = -1
$maxH = -1
$aGt0 = 0
$nearCount = 0
$timeStamps = New-Object System.Collections.Generic.List[string]
$linkCount = 0
$linkHitCount = 0
$phaseCount = 0
$guardCount = 0
$noiseCount = 0

foreach ($line in Get-Content -Path $LogPath) {

    $m = $nearPattern.Match($line)
    if ($m.Success) {
        $nearCount++
        $a = [int]$m.Groups['A'].Value
        $h = [int]$m.Groups['H'].Value
        if ($a -gt $maxA) { $maxA = $a }
        if ($h -gt $maxH) { $maxH = $h }
        if ($a -gt 0) { $aGt0++ }
        $ts = $m.Groups['ts'].Value
        if ($ts) { [void]$timeStamps.Add($ts) }
    }

    if ($linkPattern.IsMatch($line)) { $linkCount++ }
    if ($linkHitPattern.IsMatch($line)) { $linkHitCount++ }
    if ($phasePattern.IsMatch($line)) { $phaseCount++ }
    if ($guardPattern.IsMatch($line)) { $guardCount++ }
    if ($noisePattern.IsMatch($line)) { $noiseCount++ }
}

Write-Host "=== Carjack Quick Report ==="
Write-Host "Log: $LogPath"
Write-Host "Near-events samples: $nearCount"
Write-Host "A max: $maxA"
Write-Host "H max: $maxH"
Write-Host "A>0 samples: $aGt0"
Write-Host "CarjackLink lines: $linkCount"
Write-Host "CarjackLink hit-lines (P+/A+): $linkHitCount"
Write-Host "CarjackPhase lines: $phaseCount"
Write-Host "GUARD-TGT lines: $guardCount"
Write-Host "Noise read_bytes@0xE00000037: $noiseCount"

$window = "n/a"
if ($timeStamps.Count -gt 0) {
    $window = "{0} -> {1}" -f $timeStamps[0], $timeStamps[$timeStamps.Count - 1]
}
Write-Host "Carjack near-events window: $window"
