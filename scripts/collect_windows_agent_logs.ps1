param(
    [int]$LastMinutes = 120,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$logsRoot = Join-Path $env:LOCALAPPDATA "AnnounceFlow\logs"
if (!(Test-Path $logsRoot)) {
    Write-Error "Agent log directory not found: $logsRoot"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $env:TEMP "AnnounceFlowDiag"
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stageDir = Join-Path $OutputDir "agent_diag_$timestamp"
$null = New-Item -ItemType Directory -Path $stageDir -Force

$cutoff = (Get-Date).AddMinutes(-1 * [math]::Abs($LastMinutes))

# Copy main log files (if present)
$mainLogs = @("agent.log", "agent_stream.log")
foreach ($name in $mainLogs) {
    $src = Join-Path $logsRoot $name
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $stageDir $name) -Force
    }
}

# Copy stream attempt JSON files
$attemptDir = Join-Path $logsRoot "stream_attempts"
$attemptOutDir = Join-Path $stageDir "stream_attempts"
$null = New-Item -ItemType Directory -Path $attemptOutDir -Force

$attemptFiles = @()
if (Test-Path $attemptDir) {
    $attemptFiles = Get-ChildItem -Path $attemptDir -Filter "stream_attempt_*.json" -File |
        Where-Object { $_.LastWriteTime -ge $cutoff } |
        Sort-Object LastWriteTime -Descending

    if ($attemptFiles.Count -eq 0) {
        $attemptFiles = Get-ChildItem -Path $attemptDir -Filter "stream_attempt_*.json" -File |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 30
    }

    foreach ($f in $attemptFiles) {
        Copy-Item -Path $f.FullName -Destination (Join-Path $attemptOutDir $f.Name) -Force
    }
}

# Write summary for quick triage
$summary = @()
foreach ($f in $attemptFiles) {
    try {
        $json = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json
        $summary += [PSCustomObject]@{
            file        = $f.Name
            modified_at = $f.LastWriteTime.ToString("s")
            attempt_id  = $json.attempt_id
            stage       = $json.stage
            error_code  = $json.error_code
            error_type  = $json.error_type
            success     = $json.success
            speaker     = $json.speaker_name
            target      = "$($json.target_host):$($json.target_port)"
            packet_count = $json.packet_count
        }
    }
    catch {
        $summary += [PSCustomObject]@{
            file        = $f.Name
            modified_at = $f.LastWriteTime.ToString("s")
            attempt_id  = ""
            stage       = "parse_failed"
            error_code  = "parse_failed"
            error_type  = $_.Exception.GetType().Name
            success     = $false
            speaker     = ""
            target      = ""
            packet_count = ""
        }
    }
}

$summary | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $stageDir "attempt_summary.json") -Encoding UTF8
$summary | Export-Csv -Path (Join-Path $stageDir "attempt_summary.csv") -NoTypeInformation -Encoding UTF8

$meta = [PSCustomObject]@{
    collected_at   = (Get-Date).ToString("s")
    logs_root      = $logsRoot
    last_minutes   = $LastMinutes
    cutoff         = $cutoff.ToString("s")
    attempt_count  = $attemptFiles.Count
}
$meta | ConvertTo-Json -Depth 3 | Set-Content -Path (Join-Path $stageDir "meta.json") -Encoding UTF8

$zipPath = Join-Path $OutputDir "agent_diag_$timestamp.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $zipPath -Force

Write-Host "Created diagnostic bundle:"
Write-Host $zipPath
