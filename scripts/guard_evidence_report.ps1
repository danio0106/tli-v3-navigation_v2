param(
    [string]$LogPath = "",
    [string]$GuardAddr = "",
    [string]$ControlAddrs = "",
    [int]$TopControls = 5,
    [int]$PathNearTolerance = 250
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

if (-not $GuardAddr) {
    Write-Host "Provide -GuardAddr (example: 0x1C50AEEB890)"
    exit 1
}

function ConvertTo-AddrKey([string]$a) {
    if (-not $a) { return "" }
    $v = $a.Trim()
    if ($v.StartsWith("0x") -or $v.StartsWith("0X")) {
        $v = $v.Substring(2)
    }
    return ("0x" + $v.ToUpperInvariant())
}

function Get-OrNewRecord([hashtable]$map, [string]$addr) {
    if (-not $map.ContainsKey($addr)) {
        $map[$addr] = [pscustomobject]@{
            Addr = $addr
            Samples = New-Object System.Collections.Generic.List[object]
            DtMin = [double]::PositiveInfinity
            DtMax = [double]::NegativeInfinity
            DtSum = 0.0
            CfgTriples = New-Object System.Collections.Generic.HashSet[string]
            RoleSamples = 0
            EliteCount = 0
            BossCount = 0
            RaritySeen = New-Object System.Collections.Generic.HashSet[string]
            AbpValues = New-Object System.Collections.Generic.HashSet[string]
        }
    }
    return $map[$addr]
}

$guardNorm = ConvertTo-AddrKey $GuardAddr

$trackRx = [regex]'^(?<ts>\d{2}:\d{2}:\d{2}).*\[EScanTrack\]\s+0x(?<addr>[0-9A-Fa-f]+)\s+pos=\((?<x>-?\d+),(?<y>-?\d+)\)\s+dt=(?<dt>-?\d+)\s+abp=(?<abp>.+)$'
$cfgRx = [regex]'^\d{2}:\d{2}:\d{2}.*\[CfgDiag\]\s+0x(?<addr>[0-9A-Fa-f]+)\s+.*cfg_id=(?<cid>-?\d+)\s+cfg_type=(?<ctype>-?\d+)\s+cfg_eid=(?<ceid>-?\d+)'
$roleRx = [regex]'^\d{2}:\d{2}:\d{2}.*\[RoleProbe\]\s+sample\s+ent=0x(?<addr>[0-9A-Fa-f]+)\s+off=\+(?<off>[0-9A-Fa-fx]+)\s+elite=(?<elite>-?\d+)\s+boss=(?<boss>-?\d+)\s+rarity=(?<rar>-?\d+)'
$trackDiagRx = [regex]'^\d{2}:\d{2}:\d{2}.*\[TrackDiag\]\s+0x(?<addr>[0-9A-Fa-f]+)\s+cfg_id=(?<cid>-?\d+)\s+cfg_type=(?<ctype>-?\d+)\s+cfg_eid=(?<ceid>-?\d+)\s+qa_src=(?<qas>-?\d+)\s+qa_pid=(?<qpid>-?\d+)\s+qa_rarity=(?<qrar>-?\d+)\s+qa_vfx=(?<qvfx>-?\d+)\s+qa_is_monster=(?<qmon>-?\d+)\s+elite=(?<elite>-?\d+)\s+boss=(?<boss>-?\d+)\s+role_rarity=(?<rrar>-?\d+)'

$records = @{}

foreach ($line in Get-Content -Path $LogPath) {
    $mT = $trackRx.Match($line)
    if ($mT.Success) {
        $addr = ConvertTo-AddrKey ("0x" + $mT.Groups['addr'].Value)
        $rec = Get-OrNewRecord $records $addr

        $x = [double]$mT.Groups['x'].Value
        $y = [double]$mT.Groups['y'].Value
        $dt = [double]$mT.Groups['dt'].Value
        $abp = $mT.Groups['abp'].Value.Trim()

        [void]$rec.Samples.Add([pscustomobject]@{ X = $x; Y = $y; Dt = $dt })
        if ($dt -lt $rec.DtMin) { $rec.DtMin = $dt }
        if ($dt -gt $rec.DtMax) { $rec.DtMax = $dt }
        $rec.DtSum += $dt
        if ($abp) { [void]$rec.AbpValues.Add($abp) }
        continue
    }

    $mC = $cfgRx.Match($line)
    if ($mC.Success) {
        $addr = ConvertTo-AddrKey ("0x" + $mC.Groups['addr'].Value)
        $rec = Get-OrNewRecord $records $addr
        $triple = "{0}/{1}/{2}" -f $mC.Groups['cid'].Value, $mC.Groups['ctype'].Value, $mC.Groups['ceid'].Value
        [void]$rec.CfgTriples.Add($triple)
        continue
    }

    $mR = $roleRx.Match($line)
    if ($mR.Success) {
        $addr = ConvertTo-AddrKey ("0x" + $mR.Groups['addr'].Value)
        $rec = Get-OrNewRecord $records $addr
        $elite = [int]$mR.Groups['elite'].Value
        $boss = [int]$mR.Groups['boss'].Value
        $rar = $mR.Groups['rar'].Value

        $rec.RoleSamples += 1
        if ($elite -eq 1) { $rec.EliteCount += 1 }
        if ($boss -eq 1) { $rec.BossCount += 1 }
        [void]$rec.RaritySeen.Add($rar)
        continue
    }

    $mTD = $trackDiagRx.Match($line)
    if ($mTD.Success) {
        $addr = ConvertTo-AddrKey ("0x" + $mTD.Groups['addr'].Value)
        $rec = Get-OrNewRecord $records $addr

        $triple = "{0}/{1}/{2}" -f $mTD.Groups['cid'].Value, $mTD.Groups['ctype'].Value, $mTD.Groups['ceid'].Value
        [void]$rec.CfgTriples.Add($triple)

        $rec.RoleSamples += 1
        if ([int]$mTD.Groups['elite'].Value -eq 1) { $rec.EliteCount += 1 }
        if ([int]$mTD.Groups['boss'].Value -eq 1) { $rec.BossCount += 1 }
        [void]$rec.RaritySeen.Add($mTD.Groups['rrar'].Value)
    }
}

if (-not $records.ContainsKey($guardNorm)) {
    Write-Host "Guard address not found in [EScanTrack] lines: $guardNorm"
    Write-Host "Tip: ensure this address came from current run's track matcher output."
    exit 1
}

$guardRec = $records[$guardNorm]
if ($guardRec.Samples.Count -eq 0) {
    Write-Host "Guard address has no [EScanTrack] samples: $guardNorm"
    exit 1
}

$guardPts = @($guardRec.Samples | ForEach-Object { $_ })
$tolerance2 = [double]($PathNearTolerance * $PathNearTolerance)

function Get-PathNearStats($candidateSamples, $guardSamples, [double]$tol2) {
    $nearHits = 0
    foreach ($s in $candidateSamples) {
        $hit = $false
        foreach ($g in $guardSamples) {
            $dx = $s.X - $g.X
            $dy = $s.Y - $g.Y
            if (($dx * $dx + $dy * $dy) -le $tol2) {
                $hit = $true
                break
            }
        }
        if ($hit) { $nearHits++ }
    }
    $ratio = 0.0
    if ($candidateSamples.Count -gt 0) {
        $ratio = [math]::Round(($nearHits * 100.0) / $candidateSamples.Count, 1)
    }
    return @($nearHits, $ratio)
}

$controlSet = New-Object System.Collections.Generic.HashSet[string]
if ($ControlAddrs) {
    foreach ($piece in ($ControlAddrs -split ';')) {
        $p = $piece.Trim()
        if (-not $p) { continue }
        $n = ConvertTo-AddrKey $p
        if ($n -and $n -ne $guardNorm) {
            [void]$controlSet.Add($n)
        }
    }
}

if ($controlSet.Count -eq 0) {
    $guardDtAvg = $guardRec.DtSum / [math]::Max(1, $guardRec.Samples.Count)
    $candidates = @()
    foreach ($kv in $records.GetEnumerator()) {
        $addr = $kv.Key
        $rec = $kv.Value
        if ($addr -eq $guardNorm) { continue }
        if ($rec.Samples.Count -lt 20) { continue }
        $dtAvg = $rec.DtSum / [math]::Max(1, $rec.Samples.Count)
        $dtGap = [math]::Abs($dtAvg - $guardDtAvg)
        if ($dtGap -le 1800.0) {
            $candidates += [pscustomobject]@{
                Addr = $addr
                Samples = $rec.Samples.Count
                DtGap = [math]::Round($dtGap, 1)
            }
        }
    }

    $autoControls = $candidates |
        Sort-Object @{Expression='DtGap';Descending=$false}, @{Expression='Samples';Descending=$true} |
        Select-Object -First $TopControls

    foreach ($c in $autoControls) {
        [void]$controlSet.Add($c.Addr)
    }
}

$guardDtAvgFinal = [math]::Round(($guardRec.DtSum / [math]::Max(1, $guardRec.Samples.Count)), 2)
$guardRoleRate = 0.0
if ($guardRec.RoleSamples -gt 0) {
    $guardRoleRate = [math]::Round(($guardRec.EliteCount * 100.0) / $guardRec.RoleSamples, 1)
}

$guardPath = @($guardRec.Samples | ForEach-Object { $_ })
$gFirst = $guardPath[0]
$gLast = $guardPath[$guardPath.Count - 1]
$gDisp = [math]::Round([math]::Sqrt((($gLast.X - $gFirst.X) * ($gLast.X - $gFirst.X)) + (($gLast.Y - $gFirst.Y) * ($gLast.Y - $gFirst.Y))), 1)

Write-Host "=== Guard Evidence Report ==="
Write-Host "Log: $LogPath"
Write-Host "GuardAddr: $guardNorm"
Write-Host "TrackSamples: $($guardRec.Samples.Count)"
Write-Host "TruckDistance dt(min/avg/max): $([math]::Round($guardRec.DtMin,1)) / $guardDtAvgFinal / $([math]::Round($guardRec.DtMax,1))"
Write-Host "Path displacement (first->last): $gDisp"
Write-Host "CfgTriples: $([string]::Join(', ', @($guardRec.CfgTriples)))"
Write-Host "RoleProbe: samples=$($guardRec.RoleSamples) elite_rate=$guardRoleRate% boss_hits=$($guardRec.BossCount) rarity_seen=[$([string]::Join(', ', @($guardRec.RaritySeen)))]"
Write-Host "ABP seen: [$([string]::Join(', ', @($guardRec.AbpValues)))]"

if ($controlSet.Count -eq 0) {
    Write-Host "No control cohort addresses found."
    exit 0
}

$rows = New-Object System.Collections.Generic.List[object]
foreach ($addr in $controlSet) {
    if (-not $records.ContainsKey($addr)) { continue }
    $rec = $records[$addr]
    if ($rec.Samples.Count -eq 0) { continue }

    $dtAvg = $rec.DtSum / [math]::Max(1, $rec.Samples.Count)
    $eliteRate = 0.0
    if ($rec.RoleSamples -gt 0) {
        $eliteRate = [math]::Round(($rec.EliteCount * 100.0) / $rec.RoleSamples, 1)
    }

    $nearStats = Get-PathNearStats $rec.Samples $guardPts $tolerance2
    $nearHits = $nearStats[0]
    $nearRatio = $nearStats[1]

    $rows.Add([pscustomobject]@{
        Addr = $addr
        Samples = $rec.Samples.Count
        DtAvg = [math]::Round($dtAvg, 2)
        DtGapVsGuard = [math]::Round([math]::Abs($dtAvg - $guardDtAvgFinal), 2)
        PathNearHits = $nearHits
        PathNearPct = $nearRatio
        CfgCount = $rec.CfgTriples.Count
        EliteRate = $eliteRate
        BossHits = $rec.BossCount
    })
}

$sorted = $rows |
    Sort-Object @{Expression='PathNearPct';Descending=$true}, @{Expression='DtGapVsGuard';Descending=$false}, @{Expression='Samples';Descending=$true}

Write-Host ""
Write-Host "--- Control Cohort vs Guard ---"
$sorted | Format-Table -AutoSize

Write-Host ""
Write-Host "Interpretation guide:"
Write-Host "- High PathNearPct + low DtGapVsGuard => movement profile similar to labeled guard."
Write-Host "- Consistent Cfg/Role differences between guard and controls are candidate memory discriminators."
