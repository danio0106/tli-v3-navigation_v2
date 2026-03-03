param(
    [string]$LogPath = "",
    [string]$Points = "",
    [int]$Tolerance = 220,
    [int]$Top = 10
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

if (-not $Points) {
    Write-Host "Provide -Points in format: 17104,5070;16920,4644;17214,5067"
    exit 1
}

$targetPts = @()
foreach ($chunk in ($Points -split ';')) {
    $c = $chunk.Trim()
    if (-not $c) { continue }
    $xy = $c -split ','
    if ($xy.Count -ne 2) { continue }
    $x = [double]($xy[0].Trim())
    $y = [double]($xy[1].Trim())
    $targetPts += ,@($x, $y)
}

if ($targetPts.Count -eq 0) {
    Write-Host "No valid points parsed."
    exit 1
}

$escanRx = [regex]"^(?<ts>\d{2}:\d{2}:\d{2}).*\[(?:EScan|EScanTrack)\]\s+0x(?<addr>[0-9A-Fa-f]+)\s+pos=\((?<x>-?\d+),(?<y>-?\d+)\)"

$tracks = @{}

foreach ($line in Get-Content -Path $LogPath) {
    $m = $escanRx.Match($line)
    if (-not $m.Success) { continue }

    $addr = "0x" + $m.Groups['addr'].Value.ToUpperInvariant()
    $x = [double]$m.Groups['x'].Value
    $y = [double]$m.Groups['y'].Value

    if (-not $tracks.ContainsKey($addr)) {
        $tracks[$addr] = [pscustomobject]@{
            Addr = $addr
            Samples = New-Object System.Collections.Generic.List[object]
        }
    }

    [void]$tracks[$addr].Samples.Add([pscustomobject]@{ X = $x; Y = $y })
}

if ($tracks.Count -eq 0) {
    Write-Host "No [EScan]/[EScanTrack] position lines found in log."
    exit 1
}

$tol2 = [double]($Tolerance * $Tolerance)
$rows = New-Object System.Collections.Generic.List[object]

foreach ($kv in $tracks.GetEnumerator()) {
    $track = $kv.Value
    $samples = $track.Samples
    if ($samples.Count -eq 0) { continue }

    $targetIdx = 0
    $errSum = 0.0

    foreach ($s in $samples) {
        if ($targetIdx -ge $targetPts.Count) { break }
        $tx = [double]$targetPts[$targetIdx][0]
        $ty = [double]$targetPts[$targetIdx][1]
        $dx = $s.X - $tx
        $dy = $s.Y - $ty
        $d2 = $dx * $dx + $dy * $dy
        if ($d2 -le $tol2) {
            $errSum += [math]::Sqrt($d2)
            $targetIdx++
        }
    }

    if ($targetIdx -gt 0) {
        $rows.Add([pscustomobject]@{
            Addr = $track.Addr
            MatchedPoints = $targetIdx
            AvgError = [math]::Round(($errSum / [math]::Max(1, $targetIdx)), 2)
            SampleCount = $samples.Count
        })
    }
}

$sorted = $rows |
    Sort-Object @{Expression='MatchedPoints';Descending=$true}, @{Expression='AvgError';Descending=$false}, @{Expression='SampleCount';Descending=$true} |
    Select-Object -First $Top

Write-Host "=== Guard Track Match Report ==="
Write-Host "Log: $LogPath"
Write-Host "Targets: $Points"
Write-Host "Tolerance: ±$Tolerance"
Write-Host "Candidates found: $($rows.Count)"

if (-not $sorted -or $sorted.Count -eq 0) {
    Write-Host "No matches. Try increasing -Tolerance to 300 or use a log with more [EScan] samples."
    exit 0
}

$sorted | Format-Table -AutoSize
