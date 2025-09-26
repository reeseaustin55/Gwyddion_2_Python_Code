@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM -------------------------------------------------------------
REM User configuration
REM -------------------------------------------------------------
set "IMAGE_ROOT=D:\AFM Images\Video Processing\processed"
set "RAW_SOURCE_DIR=D:\AFM Images\Video Processing"
set "FFMPEG_PATH=C:\Program Files\ffmpeg-2025-02-24-git-6232f416b1-full_build\bin\ffmpeg.exe"
set "VIDEO_DURATION_SECONDS=30"
set "FRAME_RATE=30"
set "PIXEL_FORMAT=yuv420p"
set "SPLIT_SCANS=1"
set "INCLUDE_ACF=1"

REM -------------------------------------------------------------
REM Invoke the companion PowerShell script
REM -------------------------------------------------------------
set "SCRIPT_PATH=%~dp0render_processed_videos.ps1"
if not exist "%SCRIPT_PATH%" (
    echo ERROR: Missing PowerShell helper "%SCRIPT_PATH%".
    exit /b 1
)

set "SPLIT_ARG=-SplitScans:$false"
if /i "%SPLIT_SCANS%"=="1" set "SPLIT_ARG=-SplitScans:$true"

set "ACF_ARG=-IncludeAcf:$false"
if /i "%INCLUDE_ACF%"=="1" set "ACF_ARG=-IncludeAcf:$true"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_PATH%" ^
  -ImageRoot "%IMAGE_ROOT%" ^
  -RawSource "%RAW_SOURCE_DIR%" ^
  -FfmpegPath "%FFMPEG_PATH%" ^
  -VideoDuration %VIDEO_DURATION_SECONDS% ^
  -FrameRate %FRAME_RATE% ^
  -PixelFormat "%PIXEL_FORMAT%" ^
  %SPLIT_ARG% ^
  %ACF_ARG%

set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Video stitching failed with exit code %EXITCODE%.
  exit /b %EXITCODE%
)

echo.
echo Videos created successfully.
exit /b 0
