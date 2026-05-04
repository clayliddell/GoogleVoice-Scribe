param(
    [string]$SourceSession,
    [double]$DurationSeconds = 900,
    [double]$WarmupDurationSeconds = 30,
    [int]$MeasuredRuns = 2,
    [int]$WarmupRuns = 1,
    [int]$Port = 8876,
    [int]$SampleRate = 48000,
    [int]$ChunkFrames = 4096,
    [int]$PollTimeoutSeconds = 14400,
    [ValidateSet("gpu", "cpu")]
    [string[]]$ModeOrder = @("gpu", "cpu")
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceRoot = Join-Path $repoRoot "service"
$benchmarkScript = Join-Path $repoRoot "scripts\benchmark-pipeline.py"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$serviceUrl = "http://127.0.0.1:$Port"
$summaryRoot = Join-Path $repoRoot "benchmarks\cpu-gpu"
$logRoot = Join-Path $summaryRoot "logs"
New-Item -ItemType Directory -Path $summaryRoot -Force | Out-Null
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$records = New-Object System.Collections.Generic.List[object]
$startedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]
$envKeys = @("GV_SERVICE_HOST", "GV_SERVICE_PORT", "GV_FORCE_CPU", "GV_WARM_GRANITE_ON_CALL_START")
$savedEnv = @{}
foreach ($key in $envKeys) {
    $savedEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
}

function Start-BenchmarkServer {
    param(
        [ValidateSet("gpu", "cpu")]
        [string]$Mode
    )

    $forceCpu = if ($Mode -eq "cpu") { "1" } else { "0" }
    [Environment]::SetEnvironmentVariable("GV_SERVICE_HOST", "127.0.0.1", "Process")
    [Environment]::SetEnvironmentVariable("GV_SERVICE_PORT", [string]$Port, "Process")
    [Environment]::SetEnvironmentVariable("GV_FORCE_CPU", $forceCpu, "Process")
    [Environment]::SetEnvironmentVariable("GV_WARM_GRANITE_ON_CALL_START", "1", "Process")

    $stdout = Join-Path $logRoot "${timestamp}_${Mode}_server.out.log"
    $stderr = Join-Path $logRoot "${timestamp}_${Mode}_server.err.log"
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @("-m", "app.cli", "--host", "127.0.0.1", "--port", [string]$Port) `
        -WorkingDirectory $serviceRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    return $process
}

function Stop-BenchmarkServer {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) {
        return
    }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $Process.Id -Timeout 20 -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warning "Failed to stop benchmark server PID $($Process.Id): $_"
    }
}

function Wait-ForHealth {
    param(
        [bool]$ExpectedForceCpu,
        [System.Diagnostics.Process]$Process
    )

    $deadline = (Get-Date).AddSeconds(120)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Benchmark server exited early with code $($Process.ExitCode). Check logs under $logRoot."
        }
        try {
            $health = Invoke-RestMethod -Uri "$serviceUrl/health" -TimeoutSec 5
            if ([bool]$health.force_cpu -ne $ExpectedForceCpu) {
                throw "Health check returned force_cpu=$($health.force_cpu), expected $ExpectedForceCpu."
            }
            return $health
        } catch {
            if ((Get-Date) -ge $deadline) {
                throw "Timed out waiting for $serviceUrl/health. Last error: $_"
            }
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $serviceUrl/health."
}

function Invoke-PipelineBenchmark {
    param(
        [ValidateSet("gpu", "cpu")]
        [string]$Mode,
        [ValidateSet("warmup", "measured")]
        [string]$Phase,
        [int]$Runs
    )

    if ($Runs -le 0) {
        return @()
    }

    $arguments = @(
        $benchmarkScript,
        "--service-url", $serviceUrl,
        "--duration-seconds", [string](Get-RunDurationSeconds -Phase $Phase),
        "--sample-rate", [string]$SampleRate,
        "--chunk-frames", [string]$ChunkFrames,
        "--runs", [string]$Runs,
        "--poll-timeout-seconds", [string]$PollTimeoutSeconds,
        "--realtime-upload",
        "--progress-every", "1000"
    )
    if ($SourceSession) {
        $arguments += @("--source-session", [string]$SourceSession)
    }

    $output = & $python @arguments 2>&1
    $output | ForEach-Object { Write-Host "[$Mode/$Phase] $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "benchmark-pipeline.py failed for mode=$Mode phase=$Phase with exit code $LASTEXITCODE."
    }

    $paths = @()
    foreach ($line in $output) {
        $text = [string]$line
        if ($text -match "^report_path=(.+)$") {
            $paths += $Matches[1].Trim()
        }
    }
    if ($paths.Count -ne $Runs) {
        throw "Expected $Runs report_path lines for mode=$Mode phase=$Phase, found $($paths.Count)."
    }
    return $paths
}

function Read-RunRecord {
    param(
        [ValidateSet("gpu", "cpu")]
        [string]$Mode,
        [ValidateSet("warmup", "measured")]
        [string]$Phase,
        [string]$ReportPath
    )

    $payload = Read-JsonFile -Path $ReportPath
    $timings = $payload.timings
    return [pscustomobject]@{
        mode = $Mode
        phase = $Phase
        run_index = [int]$payload.run_index
        report_path = [string]$ReportPath
        final_status = [string]$payload.final_status
        force_cpu = [bool]$payload.health.force_cpu
        duration_seconds = [double]$payload.duration_seconds
        realtime_upload = [bool]$payload.realtime_upload
        upload_wall_seconds = Get-Number $payload.upload_wall_seconds
        post_call_transcription_wall_seconds = Get-Number $payload.post_call_transcription_wall_seconds
        post_call_real_time_factor = Get-Number $payload.post_call_real_time_factor
        total_replay_wall_seconds = Get-Number $payload.total_replay_wall_seconds
        transcription_total_seconds = Get-Number $timings.transcription_total_seconds
        mixed_transcribe_seconds = Get-Number $timings.mixed_transcribe_seconds
        incremental_mixed_transcribe_seconds = Get-Number $timings.incremental_mixed_transcribe_seconds
        you_reference_transcribe_seconds = Get-Number $timings.you_reference_transcribe_seconds
        callee_reference_transcribe_seconds = Get-Number $timings.callee_reference_transcribe_seconds
        title_generation_seconds = Get-Number $timings.title_generation_seconds
        conversation_build_seconds = Get-Number $timings.conversation_build_seconds
        gpu_before_memory_used_mib = Get-Number $payload.gpu_before.memory_used_mib
        gpu_after_memory_used_mib = Get-Number $payload.gpu_after.memory_used_mib
        gpu_after_utilization_percent = Get-Number $payload.gpu_after.utilization_gpu_percent
        final_session_dir = [string]$payload.final_session_dir
    }
}

function Build-Summary {
    param([System.Collections.Generic.List[object]]$Records)

    $aggregates = @()
    foreach ($mode in $ModeOrder) {
        $items = @($Records | Where-Object { $_.mode -eq $mode -and $_.phase -eq "measured" })
        if (-not $items) {
            continue
        }
        $aggregates += [pscustomobject]@{
            mode = $mode
            measured_runs = $items.Count
            avg_post_call_transcription_wall_seconds = Average-Property $items "post_call_transcription_wall_seconds"
            avg_post_call_real_time_factor = Average-Property $items "post_call_real_time_factor"
            avg_transcription_total_seconds = Average-Property $items "transcription_total_seconds"
            avg_mixed_transcribe_seconds = Average-Property $items "mixed_transcribe_seconds"
            avg_incremental_mixed_transcribe_seconds = Average-Property $items "incremental_mixed_transcribe_seconds"
            avg_you_reference_transcribe_seconds = Average-Property $items "you_reference_transcribe_seconds"
            avg_callee_reference_transcribe_seconds = Average-Property $items "callee_reference_transcribe_seconds"
            avg_title_generation_seconds = Average-Property $items "title_generation_seconds"
            avg_gpu_after_memory_used_mib = Average-Property $items "gpu_after_memory_used_mib"
            avg_gpu_after_utilization_percent = Average-Property $items "gpu_after_utilization_percent"
        }
    }

    $speedups = [ordered]@{}
    $gpu = $aggregates | Where-Object { $_.mode -eq "gpu" } | Select-Object -First 1
    $cpu = $aggregates | Where-Object { $_.mode -eq "cpu" } | Select-Object -First 1
    if ($gpu -and $cpu) {
        $speedups["post_call_transcription_wall_seconds_cpu_over_gpu"] = Divide-Nullable `
            $cpu.avg_post_call_transcription_wall_seconds `
            $gpu.avg_post_call_transcription_wall_seconds
        $speedups["transcription_total_seconds_cpu_over_gpu"] = Divide-Nullable `
            $cpu.avg_transcription_total_seconds `
            $gpu.avg_transcription_total_seconds
    }

    return [pscustomobject]@{
        benchmark = "google_voice_cpu_gpu_comparison"
        created_at = [DateTime]::UtcNow.ToString("o")
        service_url = $serviceUrl
        duration_seconds = $DurationSeconds
        warmup_duration_seconds = $WarmupDurationSeconds
        sample_rate = $SampleRate
        chunk_frames = $ChunkFrames
        measured_runs_per_mode = $MeasuredRuns
        warmup_runs_per_mode = $WarmupRuns
        mode_order = $ModeOrder
        source_session = if ($SourceSession) { [string]$SourceSession } else { "" }
        aggregates = $aggregates
        speedups = $speedups
        reports = $Records
    }
}

function Average-Property {
    param(
        [object[]]$Items,
        [string]$Name
    )

    $values = @(
        foreach ($item in $Items) {
            $value = $item.$Name
            if ($null -ne $value -and $value -ne "") {
                [double]$value
            }
        }
    )
    if ($values.Count -eq 0) {
        return $null
    }
    return [Math]::Round((($values | Measure-Object -Average).Average), 3)
}

function Read-JsonFile {
    param([string]$Path)

    $resolvedPath = Resolve-Path -LiteralPath $Path
    return [System.IO.File]::ReadAllText($resolvedPath.ProviderPath) | ConvertFrom-Json
}

function Get-RunDurationSeconds {
    param(
        [ValidateSet("warmup", "measured")]
        [string]$Phase
    )

    if ($Phase -eq "warmup") {
        return $WarmupDurationSeconds
    }
    return $DurationSeconds
}

function Divide-Nullable {
    param($Numerator, $Denominator)
    if ($null -eq $Numerator -or $null -eq $Denominator -or [double]$Denominator -eq 0) {
        return $null
    }
    return [Math]::Round(([double]$Numerator / [double]$Denominator), 3)
}

function Get-Number {
    param($Value)
    if ($null -eq $Value -or $Value -eq "") {
        return $null
    }
    return [double]$Value
}

function Test-TcpPortOpen {
    param([int]$Port)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(300)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

try {
    foreach ($mode in $ModeOrder) {
        if (Test-TcpPortOpen -Port $Port) {
            throw "Port $Port is already accepting connections. Stop that service or rerun with -Port <free-port>."
        }

        $process = Start-BenchmarkServer -Mode $mode
        $startedProcesses.Add($process) | Out-Null
        $expectedForceCpu = $mode -eq "cpu"
        $health = Wait-ForHealth -ExpectedForceCpu $expectedForceCpu -Process $process
        Write-Output "server_mode=$mode pid=$($process.Id) force_cpu=$($health.force_cpu)"

        try {
            if ($WarmupRuns -gt 0) {
                Write-Output "warmup_mode=$mode runs=$WarmupRuns"
                $warmupReports = Invoke-PipelineBenchmark -Mode $mode -Phase "warmup" -Runs $WarmupRuns
                foreach ($report in $warmupReports) {
                    $records.Add((Read-RunRecord -Mode $mode -Phase "warmup" -ReportPath $report)) | Out-Null
                }
            }

            Write-Output "measured_mode=$mode runs=$MeasuredRuns"
            $measuredReports = Invoke-PipelineBenchmark -Mode $mode -Phase "measured" -Runs $MeasuredRuns
            foreach ($report in $measuredReports) {
                $records.Add((Read-RunRecord -Mode $mode -Phase "measured" -ReportPath $report)) | Out-Null
            }
        } finally {
            Stop-BenchmarkServer -Process $process
            [void]$startedProcesses.Remove($process)
            Start-Sleep -Seconds 3
        }
    }

    $summary = Build-Summary -Records $records
    $jsonPath = Join-Path $summaryRoot "${timestamp}_cpu_gpu_summary.json"
    $csvPath = Join-Path $summaryRoot "${timestamp}_cpu_gpu_summary.csv"
    $summary | ConvertTo-Json -Depth 24 | Set-Content -Path $jsonPath -Encoding UTF8
    $records.ToArray() | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8

    Write-Output ""
    Write-Output "cpu_gpu_summary:"
    $summary.aggregates | Sort-Object mode | Format-Table `
        mode,
        measured_runs,
        avg_post_call_transcription_wall_seconds,
        avg_post_call_real_time_factor,
        avg_transcription_total_seconds,
        avg_mixed_transcribe_seconds,
        avg_title_generation_seconds `
        -AutoSize
    Write-Output "summary_json=$jsonPath"
    Write-Output "summary_csv=$csvPath"
} finally {
    foreach ($process in @($startedProcesses)) {
        Stop-BenchmarkServer -Process $process
    }
    foreach ($key in $envKeys) {
        if ($null -eq $savedEnv[$key]) {
            [Environment]::SetEnvironmentVariable($key, $null, "Process")
        } else {
            [Environment]::SetEnvironmentVariable($key, $savedEnv[$key], "Process")
        }
    }
}
