# PowerShell helper invoked by render_processed_videos.bat
param(
    [Parameter(Mandatory=$true)]
    [string]$ImageRoot,
    [string]$RawSource,
    [string]$FfmpegPath = 'ffmpeg',
    [double]$VideoDuration = 0,
    [double]$FrameRate = 30,
    [string]$PixelFormat = 'yuv420p',
    [switch]$SplitScans,
    [switch]$IncludeAcf
)

$ErrorActionPreference = 'Stop'

function Resolve-FullPath {
    param([string]$Path)
    if (-not $Path) { return $null }
    $expanded = [System.Environment]::ExpandEnvironmentVariables($Path)
    try {
        return (Resolve-Path -LiteralPath $expanded).ProviderPath
    } catch {
        return $expanded
    }
}

function Format-TimeMultiplier {
    param($Multiplier)
    try {
        $value = [double]$Multiplier
    } catch {
        $value = 0.0
    }
    if (-not $value -or $value -lt 0) { $value = 1.0 }
    $rounded = [Math]::Round($value, 2)
    $text = $rounded.ToString('0.##', [System.Globalization.CultureInfo]::InvariantCulture)
    if ([string]::IsNullOrWhiteSpace($text)) { $text = '1' }
    return "$text" + 'X'
}

function Get-RepeatCounts {
    param(
        [double[]]$Durations,
        [int]$FrameCount,
        [double]$DefaultDuration,
        [double]$FrameRate
    )
    if ($FrameCount -le 0) { return @() }
    if ($FrameRate -le 0) { return @() }
    if ($DefaultDuration -le 0) {
        $DefaultDuration = 1.0 / $FrameRate
    }
    $normalized = @()
    for ($i = 0; $i -lt $FrameCount; $i++) {
        $value = $DefaultDuration
        if ($Durations -and $i -lt $Durations.Count) {
            $candidate = $Durations[$i]
            if ($candidate -and $candidate -gt 0) { $value = [double]$candidate }
        }
        $normalized += $value
    }
    $totalDuration = ($normalized | Measure-Object -Sum).Sum
    if ($totalDuration -le 0) { $totalDuration = $DefaultDuration * $FrameCount }
    $targetTotalFrames = [int][Math]::Round($totalDuration * $FrameRate)
    if ($targetTotalFrames -lt $FrameCount) { $targetTotalFrames = $FrameCount }
    $repeats = @()
    $produced = 0
    $accumulator = 0.0
    foreach ($value in $normalized) {
        $accumulator += $value * $FrameRate
        $count = [int][Math]::Round($accumulator) - $produced
        if ($count -le 0) { $count = 1 }
        $repeats += $count
        $produced += $count
    }
    if ($repeats.Count -gt 0) {
        $diff = $targetTotalFrames - $produced
        if ($diff -ne 0) {
            $last = $repeats.Count - 1
            $repeats[$last] = [Math]::Max(1, $repeats[$last] + $diff)
        }
    }
    return ,$repeats
}

function Get-PngSize {
    param([string]$Path)
    $signature = [byte[]](137,80,78,71,13,10,26,10)
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $reader = New-Object System.IO.BinaryReader($stream)
        $magic = $reader.ReadBytes(8)
        if (-not [System.Linq.Enumerable]::SequenceEqual($magic, $signature)) {
            return $null
        }
        $lengthBytes = $reader.ReadBytes(4)
        if ($lengthBytes.Length -ne 4) { return $null }
        [Array]::Reverse($lengthBytes)
        $length = [System.BitConverter]::ToUInt32($lengthBytes, 0)
        $chunkType = [System.Text.Encoding]::ASCII.GetString($reader.ReadBytes(4))
        if ($chunkType -ne 'IHDR') { return $null }
        $data = $reader.ReadBytes($length)
        if ($data.Length -lt 8) { return $null }
        $widthBytes = $data[0..3]
        $heightBytes = $data[4..7]
        [Array]::Reverse($widthBytes)
        [Array]::Reverse($heightBytes)
        $width = [System.BitConverter]::ToUInt32($widthBytes, 0)
        $height = [System.BitConverter]::ToUInt32($heightBytes, 0)
        return @{ Width = $width; Height = $height }
    } finally {
        $stream.Dispose()
    }
}

function Build-FrameInfo {
    param(
        [System.IO.FileInfo[]]$Files,
        [string]$RawDirectory
    )
    $frames = @()
    foreach ($file in $Files) {
        $baseName = $file.BaseName
        $match = [Regex]::Match($baseName, '_channel\d+_(.+)$')
        if ($match.Success) {
            $rawBase = $match.Groups[1].Value
        } else {
            $rawBase = $baseName
        }
        $timestamp = $null
        if ($RawDirectory -and (Test-Path -LiteralPath $RawDirectory)) {
            $candidate = Join-Path $RawDirectory ($rawBase + '.ibw')
            if (-not (Test-Path -LiteralPath $candidate)) {
                $candidateItem = Get-ChildItem -LiteralPath $RawDirectory -File -Filter ($rawBase + '.*') -ErrorAction SilentlyContinue | Where-Object { $_.Extension -match '(?i)\.ibw$' } | Select-Object -First 1
                if ($candidateItem) {
                    $candidate = $candidateItem.FullName
                } else {
                    $candidate = $null
                }
            }
            if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                $timestamp = (Get-Item -LiteralPath $candidate).LastWriteTime
            }
        }
        if (-not $timestamp) {
            $timestamp = $file.LastWriteTime
        }
        $frames += [PSCustomObject]@{
            Path = $file.FullName
            Base = $rawBase
            Timestamp = $timestamp
        }
    }
    return ,$frames
}

function Build-Durations {
    param(
        [object[]]$Frames,
        [double]$TargetDuration,
        [double]$TimeMultiplier
    )
    $count = $Frames.Count
    if ($count -le 0) { return @() }
    $fallback = $TargetDuration / [Math]::Max(1, $count)
    if ($fallback -le 0) { $fallback = 0.1 }
    $aligned = @()
    foreach ($frame in $Frames) { $aligned += $frame.Timestamp }
    while ($aligned.Count -lt $count) { $aligned += $null }
    $intervals = @()
    for ($i = 0; $i -lt $count - 1; $i++) {
        $current = $aligned[$i]
        $next = $aligned[$i + 1]
        if (-not $current -or -not $next -or $next -lt $current) {
            $intervals += $null
        } else {
            $intervals += [double]($next - $current).TotalSeconds
        }
    }
    if ($count -gt 0) {
        if ($intervals.Count -gt 0) {
            $intervals += $intervals[$intervals.Count - 1]
        } else {
            $intervals += $null
        }
    }
    $durations = @()
    foreach ($interval in $intervals) {
        if ($TimeMultiplier -and $TimeMultiplier -gt 0 -and $interval -and $interval -gt 0) {
            $durations += ($interval / $TimeMultiplier)
        } else {
            $durations += $fallback
        }
    }
    while ($durations.Count -lt $count) { $durations += $fallback }
    if ($durations.Count -gt $count) {
        $durations = $durations[0..($count - 1)]
    }
    return ,$durations
}

function Compute-ActualDuration {
    param([object[]]$Frames)
    $valid = $Frames | Where-Object { $_.Timestamp } | Sort-Object Timestamp
    if ($valid.Count -lt 2) { return 0.0 }
    $first = $valid[0].Timestamp
    $last = $valid[$valid.Count - 1].Timestamp
    if ($last -lt $first) { return 0.0 }
    return [double]($last - $first).TotalSeconds
}

function Write-Manifest {
    param(
        [string]$Path,
        [object[]]$Frames,
        [double[]]$Durations,
        [double]$DefaultDuration,
        [double[]]$RepeatCounts,
        [bool]$UseRepeats
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $writer = New-Object System.IO.StreamWriter($Path, $false, $encoding)
    try {
        if ($UseRepeats -and $RepeatCounts -and $RepeatCounts.Count -eq $Frames.Count) {
            for ($i = 0; $i -lt $Frames.Count; $i++) {
                $filePath = $Frames[$i].Path.Replace([char]34, '"')
                $count = [Math]::Max(1, [int]$RepeatCounts[$i])
                for ($r = 0; $r -lt $count; $r++) {
                    $writer.WriteLine('file "{0}"', $filePath)
                }
            }
        } else {
            $culture = [System.Globalization.CultureInfo]::InvariantCulture
            $wroteDuration = $false
            $lastEscaped = $null
            for ($i = 0; $i -lt $Frames.Count; $i++) {
                $filePath = $Frames[$i].Path.Replace([char]34, '"')
                $writer.WriteLine('file "{0}"', $filePath)
                $lastEscaped = $filePath
                $duration = $null
                if ($Durations -and $i -lt $Durations.Count) {
                    $duration = $Durations[$i]
                }
                if (-not $duration -and $DefaultDuration -gt 0) {
                    $duration = $DefaultDuration
                }
                if (-not $duration) { continue }
                try {
                    $durationValue = [double]$duration
                } catch {
                    $durationValue = 0.0
                }
                if ($durationValue -le 0) { $durationValue = 0.001 }
                $writer.WriteLine($culture, 'duration {0:F9}', $durationValue)
                $wroteDuration = $true
            }
            if ($wroteDuration -and $lastEscaped) {
                $writer.WriteLine('file "{0}"', $lastEscaped)
            }
        }
    } finally {
        $writer.Dispose()
    }
}

function Render-Video {
    param(
        [object[]]$Frames,
        [int]$ChannelNumber,
        [string]$ChannelPath,
        [string]$BaseName,
        [string]$DataKind,
        [string]$Direction,
        [double]$VideoDuration,
        [double]$FrameRate,
        [string]$PixelFormat,
        [string]$FfmpegPath
    )
    if (-not $Frames -or $Frames.Count -eq 0) { return $null }
    $actualDuration = Compute-ActualDuration -Frames $Frames
    $targetDuration = $VideoDuration
    if ($targetDuration -le 0) {
        if ($actualDuration -gt 0) {
            $targetDuration = $actualDuration
        } else {
            $targetDuration = [Math]::Max($Frames.Count * 0.1, 10.0)
        }
    }
    if ($targetDuration -le 0) {
        $targetDuration = [Math]::Max($Frames.Count * 0.1, 1.0)
    }
    $timeMultiplier = $null
    if ($actualDuration -gt 0 -and $targetDuration -gt 0) {
        $timeMultiplier = $actualDuration / $targetDuration
    }
    $durations = Build-Durations -Frames $Frames -TargetDuration $targetDuration -TimeMultiplier $timeMultiplier
    $defaultDuration = $targetDuration / [Math]::Max(1, $Frames.Count)
    $useRepeats = $FrameRate -gt 0
    $repeatCounts = $null
    if ($useRepeats) {
        $repeatCounts = Get-RepeatCounts -Durations $durations -FrameCount $Frames.Count -DefaultDuration $defaultDuration -FrameRate $FrameRate
    }
    $tempName = 'gwyddion_frames_{0}.txt' -f ([guid]::NewGuid().ToString('N'))
    $manifest = Join-Path ([System.IO.Path]::GetTempPath()) $tempName
    Write-Manifest -Path $manifest -Frames $Frames -Durations $durations -DefaultDuration $defaultDuration -RepeatCounts $repeatCounts -UseRepeats:$useRepeats
    $ensureEven = $null
    try {
        $size = Get-PngSize -Path $Frames[0].Path
        if ($size -and (($size.Width % 2) -or ($size.Height % 2))) {
            $ensureEven = 'scale=ceil(iw/2)*2:ceil(ih/2)*2'
        }
    } catch {
        $ensureEven = $null
    }
    $suffixParts = @()
    if ($DataKind -and $DataKind -ne 'base') { $suffixParts += $DataKind }
    if ($Direction -and $Direction -ne 'full') { $suffixParts += $Direction }
    $suffixParts += (Format-TimeMultiplier $timeMultiplier)
    $suffix = ''
    if ($suffixParts.Count -gt 0) { $suffix = '_' + ($suffixParts -join '_') }
    $fileName = '{0}_channel{1}{2}.mp4' -f $BaseName, $ChannelNumber, $suffix
    $outputPath = Join-Path $ChannelPath $fileName
    $args = @('-y','-f','concat','-safe','0','-i', $manifest)
    $filters = @()
    if ($ensureEven) { $filters += $ensureEven }
    if ($filters.Count -gt 0) {
        $args += '-vf'
        $args += ($filters -join ',')
    }
    if ($useRepeats) {
        $args += '-fps_mode'
        $args += 'cfr'
        $args += '-r'
        $args += ([string]::Format([System.Globalization.CultureInfo]::InvariantCulture, '{0:F6}', $FrameRate))
    } else {
        $args += '-fps_mode'
        $args += 'vfr'
    }
    if ($PixelFormat) {
        $args += '-pix_fmt'
        $args += $PixelFormat
    }
    $args += $outputPath
    Write-Host "Rendering $outputPath" -ForegroundColor Cyan
    $process = Start-Process -FilePath $FfmpegPath -ArgumentList $args -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "ffmpeg exited with code $($process.ExitCode) while rendering $outputPath"
    }
    Remove-Item -LiteralPath $manifest -ErrorAction SilentlyContinue
    return $outputPath
}

function Split-Frames {
    param([object[]]$Frames)
    $up = @()
    $down = @()
    for ($i = 0; $i -lt $Frames.Count; $i++) {
        if ($i % 2 -eq 0) {
            $up += $Frames[$i]
        } else {
            $down += $Frames[$i]
        }
    }
    return ,@($up, $down)
}

$ImageRoot = Resolve-FullPath -Path $ImageRoot
if (-not (Test-Path -LiteralPath $ImageRoot)) {
    throw "Image directory '$ImageRoot' does not exist."
}
if ($RawSource) {
    try {
        $RawSource = Resolve-FullPath -Path $RawSource
    } catch {
        $RawSource = $null
    }
}
if ($RawSource -and -not (Test-Path -LiteralPath $RawSource)) {
    Write-Warning "Raw source directory '$RawSource' was not found. Falling back to processed timestamps."
    $RawSource = $null
}
$ffmpegResolved = $FfmpegPath
if (-not (Test-Path -LiteralPath $ffmpegResolved)) {
    try {
        $ffmpegResolved = Resolve-FullPath -Path $FfmpegPath
    } catch {
        $ffmpegResolved = $FfmpegPath
    }
}
if (-not (Test-Path -LiteralPath $ffmpegResolved)) {
    throw "ffmpeg executable '$FfmpegPath' was not found."
}
$channelDirs = Get-ChildItem -LiteralPath $ImageRoot -Directory -Filter 'channel*' | Sort-Object Name
if (-not $channelDirs) {
    throw "No channel directories were found in '$ImageRoot'."
}
$baseName = Split-Path $ImageRoot -Leaf
if ($RawSource) {
    $sourceName = Split-Path $RawSource -Leaf
    if ($sourceName) { $baseName = $sourceName }
}
if (-not $baseName) { $baseName = 'output' }
$results = @()
foreach ($channel in $channelDirs) {
    $channelName = $channel.Name
    $channelMatch = [Regex]::Match($channelName, '(?i)channel(\d+)')
    if ($channelMatch.Success) {
        $channelNumber = [int]$channelMatch.Groups[1].Value
    } else {
        $channelNumber = 0
    }
    Write-Host "Processing $channelName" -ForegroundColor Green
    $baseFiles = Get-ChildItem -LiteralPath $channel.FullName -File -Filter '*.png' | Sort-Object Name
    if ($baseFiles) {
        $frames = Build-FrameInfo -Files $baseFiles -RawDirectory $RawSource
        $video = Render-Video -Frames $frames -ChannelNumber $channelNumber -ChannelPath $channel.FullName -BaseName $baseName -DataKind 'base' -Direction 'full' -VideoDuration $VideoDuration -FrameRate $FrameRate -PixelFormat $PixelFormat -FfmpegPath $ffmpegResolved
        $entry = [PSCustomObject]@{ Channel = $channelNumber; Kind = 'base'; Direction = 'full'; Path = $video }
        $results += $entry
        if ($SplitScans.IsPresent -and $frames.Count -gt 1) {
            $parts = Split-Frames -Frames $frames
            if ($parts[0].Count -gt 0) {
                $upVideo = Render-Video -Frames $parts[0] -ChannelNumber $channelNumber -ChannelPath $channel.FullName -BaseName $baseName -DataKind 'base' -Direction 'up' -VideoDuration $VideoDuration -FrameRate $FrameRate -PixelFormat $PixelFormat -FfmpegPath $ffmpegResolved
                $results += [PSCustomObject]@{ Channel = $channelNumber; Kind = 'base'; Direction = 'up'; Path = $upVideo }
            }
            if ($parts[1].Count -gt 0) {
                $downVideo = Render-Video -Frames $parts[1] -ChannelNumber $channelNumber -ChannelPath $channel.FullName -BaseName $baseName -DataKind 'base' -Direction 'down' -VideoDuration $VideoDuration -FrameRate $FrameRate -PixelFormat $PixelFormat -FfmpegPath $ffmpegResolved
                $results += [PSCustomObject]@{ Channel = $channelNumber; Kind = 'base'; Direction = 'down'; Path = $downVideo }
            }
        }
    } else {
        Write-Warning "No PNG files found for $channelName"
    }
    if ($IncludeAcf.IsPresent) {
        $acfDir = Join-Path $channel.FullName 'acf'
        if (Test-Path -LiteralPath $acfDir) {
            $acfFiles = Get-ChildItem -LiteralPath $acfDir -File -Filter '*.png' | Sort-Object Name
            if ($acfFiles) {
                $frames = Build-FrameInfo -Files $acfFiles -RawDirectory $RawSource
                $video = Render-Video -Frames $frames -ChannelNumber $channelNumber -ChannelPath $acfDir -BaseName $baseName -DataKind 'acf' -Direction 'full' -VideoDuration $VideoDuration -FrameRate $FrameRate -PixelFormat $PixelFormat -FfmpegPath $ffmpegResolved
                $results += [PSCustomObject]@{ Channel = $channelNumber; Kind = 'acf'; Direction = 'full'; Path = $video }
                if ($SplitScans.IsPresent -and $frames.Count -gt 1) {
                    $parts = Split-Frames -Frames $frames
                    if ($parts[0].Count -gt 0) {
                        $upVideo = Render-Video -Frames $parts[0] -ChannelNumber $channelNumber -ChannelPath $acfDir -BaseName $baseName -DataKind 'acf' -Direction 'up' -VideoDuration $VideoDuration -FrameRate $FrameRate -PixelFormat $PixelFormat -FfmpegPath $ffmpegResolved
                        $results += [PSCustomObject]@{ Channel = $channelNumber; Kind = 'acf'; Direction = 'up'; Path = $upVideo }
                    }
                    if ($parts[1].Count -gt 0) {
                        $downVideo = Render-Video -Frames $parts[1] -ChannelNumber $channelNumber -ChannelPath $acfDir -BaseName $baseName -DataKind 'acf' -Direction 'down' -VideoDuration $VideoDuration -FrameRate $FrameRate -PixelFormat $PixelFormat -FfmpegPath $ffmpegResolved
                        $results += [PSCustomObject]@{ Channel = $channelNumber; Kind = 'acf'; Direction = 'down'; Path = $downVideo }
                    }
                }
            }
        }
    }
}
if ($results.Count -gt 0) {
    Write-Host ''
    Write-Host 'Rendered videos:' -ForegroundColor Yellow
    $results | Format-Table -AutoSize
} else {
    Write-Warning 'No videos were rendered.'
}
