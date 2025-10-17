#!/usr/bin/env python
"""Utility script to stitch processed images into a stabilized video."""

from __future__ import absolute_import, print_function

import argparse
import glob
import logging
import os
import sys

try:  # Python 2.7
    import Tkinter as tk
    import tkFileDialog
except ImportError:  # pragma: no cover - Python 3 fallback
    try:
        import tkinter as tk  # type: ignore
        from tkinter import filedialog as tkFileDialog  # type: ignore
    except ImportError:  # pragma: no cover - headless environment
        tk = None  # type: ignore
        tkFileDialog = None  # type: ignore

from gwyddion_batch import (
    StabilizationSettings,
    VideoSettings,
    stitch_images_to_video,
    format_time_multiplier,
)

# USER SETTINGS - MODIFY AS NEEDED -------------------------------------------
DEFAULT_IMAGE_DIRECTORY = r'D:\AFM Images'
IMAGE_PATTERN = '*.png'
OUTPUT_VIDEO = None  # Defaults to <image_directory>/<name>_<multiplier>.mp4 when None
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg-2025-02-24-git-6232f416b1-full_build\\bin\\ffmpeg.exe"  # Or just 'ffmpeg'
VIDEO_DURATION = 10.0  # seconds
VIDEO_FRAME_RATE = 30.0  # Optional constant frame rate; set to 0 to use per-frame durations
UNIFORM_FRAME_DURATION = False
PIXEL_FORMAT = 'yuv420p'
FFMPEG_EXTRA_ARGS = []  # Additional ffmpeg arguments, e.g. ['-vf', 'scale=ceil(iw/2)*2:ceil(ih/2)*2']
SOURCE_PATTERN = '*.ibw'

# Stabilization controls -----------------------------------------------------
STABILIZE_VIDEO = False
STABILIZE_MAX_DISPLACEMENT_PERCENT = 5.0
# ---------------------------------------------------------------------------


def collect_images(directory, pattern):
    """Return a sorted list of image paths in ``directory`` matching ``pattern``."""
    search_pattern = os.path.join(directory, pattern)
    paths = glob.glob(search_pattern)
    paths.sort()
    return paths


def _extract_source_base(image_path):
    base = os.path.splitext(os.path.basename(image_path))[0]
    parts = base.split('_', 3)
    if len(parts) >= 4:
        return parts[3].lower()
    return base.lower()


def collect_frame_times(image_paths, source_directory, pattern, logger):
    lookup = {}
    search_pattern = os.path.join(source_directory, pattern)
    for source_path in glob.glob(search_pattern):
        base_name = os.path.splitext(os.path.basename(source_path))[0].lower()
        try:
            lookup[base_name] = os.path.getmtime(source_path)
        except OSError:
            lookup[base_name] = None

    frame_times = []
    missing = 0
    for image_path in image_paths:
        base_name = _extract_source_base(image_path)
        timestamp = lookup.get(base_name)
        if timestamp is None:
            missing += 1
        frame_times.append(timestamp)

    if missing and logger:
        logger.warning(
            'Missing timestamp information for %d frame(s); durations will be approximated.',
            missing,
        )
    return frame_times


def compute_frame_schedule(capture_times, frame_count, video_duration,
                           force_uniform=False):
    if frame_count <= 0:
        return [], 1.0, 0.0

    if video_duration is None or video_duration <= 0:
        video_duration = frame_count * 0.1
    if video_duration <= 0:
        video_duration = 1.0

    aligned = list(capture_times[:frame_count])
    while len(aligned) < frame_count:
        aligned.append(None)

    first = None
    last = None
    for value in aligned:
        if value is None:
            continue
        if first is None:
            first = value
        last = value
    if first is None or last is None or last < first:
        actual_span = 0.0
    else:
        actual_span = float(last - first)

    if actual_span > 0:
        multiplier = actual_span / float(video_duration)
    else:
        multiplier = 1.0

    fallback = video_duration / float(frame_count)
    if fallback <= 0:
        fallback = 0.1

    if force_uniform:
        return [fallback] * frame_count, (multiplier if multiplier > 0 else 1.0), actual_span

    intervals = []
    for index in range(frame_count - 1):
        current = aligned[index]
        nxt = aligned[index + 1]
        if current is None or nxt is None or nxt < current:
            intervals.append(None)
        else:
            intervals.append(float(nxt - current))
    if intervals:
        intervals.append(intervals[-1])
    else:
        intervals.append(actual_span if actual_span > 0 else None)

    durations = []
    for interval in intervals:
        if multiplier > 0 and interval and interval > 0:
            value = interval / multiplier
            if value <= 0:
                value = fallback
        else:
            value = fallback
        durations.append(value)

    if len(durations) > frame_count:
        durations = durations[:frame_count]
    elif len(durations) < frame_count:
        durations.extend([fallback] * (frame_count - len(durations)))

    return durations, (multiplier if multiplier > 0 else 1.0), actual_span


def append_multiplier_to_filename(path, multiplier_label):
    base_path = os.path.abspath(path)
    base, ext = os.path.splitext(base_path)
    if not ext:
        ext = '.mp4'
    return base + '_' + multiplier_label + ext


def prompt_for_directory(initialdir, logger):
    """Return a directory selected via Tkinter, or ``None`` when unavailable."""
    if tkFileDialog is None:
        logger.warning('Tkinter is not available; run with --no-prompt to skip the dialog.')
        return None
    root = None
    try:
        root = tk.Tk()  # type: ignore
        root.withdraw()
        selection = tkFileDialog.askdirectory(initialdir=initialdir or '')
        if not selection:
            return None
        return selection
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning('Directory selection dialog failed: %s', exc)
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:  # pragma: no cover - best effort cleanup
                pass


def build_parser():
    parser = argparse.ArgumentParser(
        description='Stitch already processed images into a video.'
    )
    parser.add_argument(
        'image_directory', nargs='?',
        help='Directory that contains the processed image files.'
    )
    parser.add_argument('--images', dest='image_directory_opt', default=None,
                        help='Alternate way to provide the processed image directory.')
    parser.add_argument('--pattern', default=IMAGE_PATTERN,
                        help='Filename glob used to locate frames (default: %(default)s).')
    parser.add_argument('--output', dest='output_video', default=OUTPUT_VIDEO,
                        help='Optional explicit path for the rendered video file.')
    parser.add_argument('--ffmpeg', dest='ffmpeg_path', default=FFMPEG_PATH,
                        help='Path to the ffmpeg executable (default: %(default)s).')
    parser.add_argument('--video-duration', dest='video_duration', type=float,
                        default=VIDEO_DURATION,
                        help='Length of the rendered video in seconds (default: %(default)s).')
    parser.add_argument('--video-fps', dest='video_fps', type=float,
                        default=VIDEO_FRAME_RATE,
                        help='Constant playback rate in fps (default: %(default)s). Use 0 to rely on per-frame durations.')
    parser.add_argument('--uniform-frame-duration', dest='uniform_frame_duration', action='store_true',
                        default=UNIFORM_FRAME_DURATION,
                        help='Display each frame for the same duration regardless of capture timing.')
    parser.add_argument('--capture-frame-duration', dest='uniform_frame_duration', action='store_false',
                        help='Derive frame timing from capture timestamps (default).')
    parser.add_argument('--pixel-format', dest='pixel_format', default=PIXEL_FORMAT,
                        help='Pixel format for ffmpeg output (default: %(default)s).')
    parser.add_argument('--extra-arg', dest='extra_args', action='append', default=None,
                        help='Additional arguments to pass through to ffmpeg.')
    parser.add_argument('--source', dest='source_directory', default=None,
                        help='Directory containing the original IBW files (default: parent of image directory).')
    parser.add_argument('--stabilize', dest='stabilize', action='store_true',
                        default=STABILIZE_VIDEO,
                        help='Enable drift correction when stitching the video.')
    parser.add_argument('--no-stabilize', dest='stabilize', action='store_false',
                        help='Disable video stabilization.')
    parser.add_argument('--stabilize-max-percent', dest='stabilize_percent', type=float,
                        default=STABILIZE_MAX_DISPLACEMENT_PERCENT,
                        help=('Maximum percentage of the frame width allowed for inter-frame drift '
                              '(default: %(default)s).'))
    parser.add_argument('--no-prompt', dest='use_prompt', action='store_false', default=True,
                        help='Do not open a folder selection dialog when missing a directory.')
    parser.add_argument('--prompt', dest='use_prompt', action='store_true',
                        help='Force opening the folder selection dialog.')
    parser.add_argument('--log-level', dest='log_level', default='INFO',
                        help='Logging level (default: %(default)s).')
    return parser


def configure_logging(level_name):
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format='%(levelname)s: %(message)s')
    return logging.getLogger(__name__)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(args.log_level)

    image_directory = args.image_directory or args.image_directory_opt
    if args.use_prompt and (not image_directory or not os.path.isdir(image_directory)):
        initial = image_directory or DEFAULT_IMAGE_DIRECTORY
        selected = prompt_for_directory(initial, logger)
        if selected:
            image_directory = selected

    if not image_directory:
        image_directory = DEFAULT_IMAGE_DIRECTORY
        logger.info('No directory provided; defaulting to %s', image_directory)

    image_directory = os.path.abspath(image_directory)
    if not os.path.isdir(image_directory):
        logger.error('Image directory %s does not exist or is not a directory', image_directory)
        return 1

    pattern = args.pattern or IMAGE_PATTERN
    images = collect_images(image_directory, pattern)
    if not images:
        logger.error('No images matching %s found in %s', pattern, image_directory)
        return 1

    logger.info('Found %d images to stitch', len(images))

    video_duration = args.video_duration if args.video_duration is not None else VIDEO_DURATION
    if video_duration is None or video_duration <= 0:
        video_duration = VIDEO_DURATION
    if video_duration <= 0:
        video_duration = max(len(images) * 0.1, 1.0)

    frame_rate = args.video_fps if args.video_fps is not None else VIDEO_FRAME_RATE
    if frame_rate is not None:
        try:
            frame_rate = float(frame_rate)
        except Exception:
            frame_rate = None
        if frame_rate is not None and frame_rate <= 0:
            frame_rate = None

    source_directory = args.source_directory or os.path.dirname(image_directory) or image_directory
    source_directory = os.path.abspath(source_directory)
    if not os.path.isdir(source_directory):
        message = 'Source directory %s not found; using uniform frame durations.'
        if args.uniform_frame_duration:
            logger.info(message, source_directory)
        else:
            logger.warning(message, source_directory)
        capture_times = [None] * len(images)
    else:
        capture_times = collect_frame_times(images, source_directory, SOURCE_PATTERN, logger)

    frame_durations, multiplier, actual_span = compute_frame_schedule(
        capture_times,
        len(images),
        video_duration,
        force_uniform=args.uniform_frame_duration,
    )

    multiplier_label = format_time_multiplier(multiplier)

    if args.output_video:
        output_path = append_multiplier_to_filename(args.output_video, multiplier_label)
    else:
        base_name = os.path.basename(os.path.normpath(image_directory)) or 'output_video'
        default_name = '%s_%s.mp4' % (base_name, multiplier_label)
        output_path = os.path.join(image_directory, default_name)

    ffmpeg_path = args.ffmpeg_path or FFMPEG_PATH
    if not os.path.exists(ffmpeg_path):
        ffmpeg_path = args.ffmpeg_path or 'ffmpeg'

    extra_args = list(FFMPEG_EXTRA_ARGS)
    if args.extra_args:
        extra_args.extend(args.extra_args)

    stabilization = StabilizationSettings(
        enabled=args.stabilize,
        max_displacement_percent=args.stabilize_percent,
    )

    video_settings = VideoSettings(
        enabled=True,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        duration_seconds=video_duration,
        pixel_format=args.pixel_format or PIXEL_FORMAT,
        extra_args=extra_args,
        stabilization=stabilization,
        frame_rate=frame_rate,
        uniform_frame_duration=args.uniform_frame_duration,
    )

    logger.info('Capture span: %.2f seconds', actual_span)
    logger.info('Time multiplier: %s', multiplier_label)

    try:
        stitch_images_to_video(
            images,
            video_settings.output_path,
            ffmpeg_path=video_settings.ffmpeg_path,
            frame_durations=frame_durations,
            frame_duration=(video_duration / float(len(images))
                            if video_duration and len(images) else None),
            frame_rate=video_settings.frame_rate,
            pixel_format=video_settings.pixel_format,
            extra_args=video_settings.extra_args,
            logger=logger,
            stabilization=video_settings.stabilization,
        )
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        logger.error('Failed to render video: %s', exc)
        return 1

    logger.info('Video written to %s', video_settings.output_path)
    return 0


if __name__ == '__main__':  # pragma: no cover - script entry point
    sys.exit(main())
