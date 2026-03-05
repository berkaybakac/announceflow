param(
    [string]$OutputFile = ""
)

$ErrorActionPreference = "Stop"

$checks = New-Object System.Collections.Generic.List[object]
$blockers = 0
$warnings = 0

function Add-Check {
    param(
        [string]$Name,
        [string]$Status,
        [string]$Details
    )
    $checks.Add([PSCustomObject]@{
        name = $Name
        status = $Status
        details = $Details
    }) | Out-Null

    if ($Status -eq "FAIL") { $script:blockers += 1 }
    if ($Status -eq "WARN") { $script:warnings += 1 }
}

# 1) OS info
try {
    $os = Get-CimInstance Win32_OperatingSystem
    Add-Check -Name "Windows version" -Status "PASS" -Details "$($os.Caption) build=$($os.BuildNumber)"
}
catch {
    Add-Check -Name "Windows version" -Status "WARN" -Details $_.Exception.Message
}

# 2) Audio services
foreach ($svcName in @("Audiosrv", "AudioEndpointBuilder")) {
    try {
        $svc = Get-Service -Name $svcName -ErrorAction Stop
        if ($svc.Status -eq "Running") {
            Add-Check -Name "Service:$svcName" -Status "PASS" -Details "Running"
        }
        else {
            Add-Check -Name "Service:$svcName" -Status "FAIL" -Details "Status=$($svc.Status)"
        }
    }
    catch {
        Add-Check -Name "Service:$svcName" -Status "FAIL" -Details $_.Exception.Message
    }
}

# 3) Sound devices
$okSoundDevices = @()
try {
    $soundDevices = Get-CimInstance Win32_SoundDevice
    $okSoundDevices = @($soundDevices | Where-Object { $_.Status -eq "OK" })
    if ($okSoundDevices.Count -gt 0) {
        $names = ($okSoundDevices | Select-Object -ExpandProperty Name) -join "; "
        Add-Check -Name "Sound devices" -Status "PASS" -Details "$($okSoundDevices.Count) OK device(s): $names"
    }
    else {
        Add-Check -Name "Sound devices" -Status "FAIL" -Details "No Win32_SoundDevice with Status=OK"
    }
}
catch {
    Add-Check -Name "Sound devices" -Status "FAIL" -Details $_.Exception.Message
}

# 4) Default playback endpoint (best-effort COM)
try {
    $clsid = [Guid]"BCDE0395-E52F-467C-8E3D-C4579291692E"
    $type = [type]::GetTypeFromCLSID($clsid)
    $enum = [Activator]::CreateInstance($type)
    # 0=eRender, 1=eMultimedia
    $endpoint = $enum.GetDefaultAudioEndpoint(0, 1)
    $friendly = [string]$endpoint.FriendlyName
    if ([string]::IsNullOrWhiteSpace($friendly)) {
        if ($okSoundDevices.Count -gt 0) {
            Add-Check -Name "Default playback endpoint" -Status "WARN" -Details "Could not read friendly name, but sound devices exist"
        }
        else {
            Add-Check -Name "Default playback endpoint" -Status "FAIL" -Details "No default endpoint and no OK sound device"
        }
    }
    else {
        Add-Check -Name "Default playback endpoint" -Status "PASS" -Details $friendly
    }
}
catch {
    if ($okSoundDevices.Count -gt 0) {
        Add-Check -Name "Default playback endpoint" -Status "WARN" -Details "COM query failed: $($_.Exception.Message)"
    }
    else {
        Add-Check -Name "Default playback endpoint" -Status "FAIL" -Details "COM query failed and no OK sound device: $($_.Exception.Message)"
    }
}

# 5) AnnounceFlow log path writable
$logsRoot = Join-Path $env:LOCALAPPDATA "AnnounceFlow\logs"
try {
    New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
    $probe = Join-Path $logsRoot "__write_probe.tmp"
    "probe" | Set-Content -Path $probe -Encoding UTF8
    Remove-Item -Path $probe -Force
    Add-Check -Name "Agent log dir writable" -Status "PASS" -Details $logsRoot
}
catch {
    Add-Check -Name "Agent log dir writable" -Status "FAIL" -Details $_.Exception.Message
}

# 6) Latest stream attempt snapshot (best-effort)
$attemptDir = Join-Path $logsRoot "stream_attempts"
if (Test-Path $attemptDir) {
    $latest = Get-ChildItem -Path $attemptDir -Filter "stream_attempt_*.json" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -ne $latest) {
        try {
            $json = Get-Content -Path $latest.FullName -Raw | ConvertFrom-Json
            $detail = "file=$($latest.Name) stage=$($json.stage) error_code=$($json.error_code) success=$($json.success)"
            Add-Check -Name "Latest stream attempt" -Status "PASS" -Details $detail
        }
        catch {
            Add-Check -Name "Latest stream attempt" -Status "WARN" -Details "Found but parse failed: $($_.Exception.Message)"
        }
    }
    else {
        Add-Check -Name "Latest stream attempt" -Status "WARN" -Details "No stream_attempt_*.json found yet"
    }
}
else {
    Add-Check -Name "Latest stream attempt" -Status "WARN" -Details "stream_attempts directory not found"
}

if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputFile = Join-Path $env:TEMP "announceflow_windows_preflight_$timestamp.json"
}

$report = [PSCustomObject]@{
    generated_at = (Get-Date).ToString("s")
    host = $env:COMPUTERNAME
    blockers = $blockers
    warnings = $warnings
    checks = $checks
}
$report | ConvertTo-Json -Depth 6 | Set-Content -Path $OutputFile -Encoding UTF8

Write-Host "=== AnnounceFlow Windows Audio Preflight ==="
foreach ($c in $checks) {
    Write-Host ("[{0}] {1} - {2}" -f $c.status, $c.name, $c.details)
}
Write-Host ("Summary: blockers={0} warnings={1}" -f $blockers, $warnings)
Write-Host ("Report: {0}" -f $OutputFile)

if ($blockers -gt 0) {
    exit 2
}
exit 0
