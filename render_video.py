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
)

# USER SETTINGS - MODIFY AS NEEDED -------------------------------------------
DEFAULT_IMAGE_DIRECTORY = 'D\\AFM Images'
IMAGE_PATTERN = '*.png'
OUTPUT_VIDEO = None  # Defaults to <image_directory>/output_video.mp4 when None
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg-2025-02-24-git-6232f416b1-full_build\\bin\\ffmpeg.exe"  # Or just 'ffmpeg'
FRAME_DURATION = 0.1  # seconds per frame (ignored when FRAME_RATE is set)
FRAME_RATE = None  # Optional fixed frame rate
PIXEL_FORMAT = 'yuv420p'
FFMPEG_EXTRA_ARGS = []  # Additional ffmpeg arguments, e.g. ['-vf', 'scale=ceil(iw/2)*2:ceil(ih/2)*2']

# Stabilization controls -----------------------------------------------------
STABILIZE_VIDEO = False
STABILIZE_SHAKINESS = 5
STABILIZE_ACCURACY = 9
STABILIZE_STEPSIZE = 6
STABILIZE_MINCONTRAST = 0.3
STABILIZE_SMOOTHING = 15
STABILIZE_TRIPOD = True
STABILIZE_CROP_SHARED = True
# ---------------------------------------------------------------------------


def collect_images(directory, pattern):
    """Return a sorted list of image paths in ``directory`` matching ``pattern``."""
    search_pattern = os.path.join(directory, pattern)
    paths = glob.glob(search_pattern)
    paths.sort()
    return paths


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
    parser.add_argument('--frame-duration', dest='frame_duration', type=float,
                        default=FRAME_DURATION,
                        help='Frame duration in seconds (default: %(default)s).')
    parser.add_argument('--frame-rate', dest='frame_rate', type=float, default=FRAME_RATE,
                        help='Frame rate; overrides frame duration when provided.')
    parser.add_argument('--pixel-format', dest='pixel_format', default=PIXEL_FORMAT,
                        help='Pixel format for ffmpeg output (default: %(default)s).')
    parser.add_argument('--extra-arg', dest='extra_args', action='append', default=None,
                        help='Additional arguments to pass through to ffmpeg.')
    parser.add_argument('--stabilize', dest='stabilize', action='store_true',
                        default=STABILIZE_VIDEO,
                        help='Enable drift correction and optional cropping.')
    parser.add_argument('--no-stabilize', dest='stabilize', action='store_false',
                        help='Disable video stabilization.')
    parser.add_argument('--stabilize-shakiness', dest='stabilize_shakiness', type=int,
                        default=STABILIZE_SHAKINESS,
                        help='Vidstab shakiness parameter (default: %(default)s).')
    parser.add_argument('--stabilize-accuracy', dest='stabilize_accuracy', type=int,
                        default=STABILIZE_ACCURACY,
                        help='Vidstab accuracy parameter (default: %(default)s).')
    parser.add_argument('--stabilize-stepsize', dest='stabilize_stepsize', type=int,
                        default=STABILIZE_STEPSIZE,
                        help='Vidstab step size (default: %(default)s).')
    parser.add_argument('--stabilize-mincontrast', dest='stabilize_mincontrast', type=float,
                        default=STABILIZE_MINCONTRAST,
                        help='Vidstab minimum contrast threshold (default: %(default)s).')
    parser.add_argument('--stabilize-smoothing', dest='stabilize_smoothing', type=int,
                        default=STABILIZE_SMOOTHING,
                        help='Vidstab smoothing radius (default: %(default)s).')
    parser.add_argument('--stabilize-tripod', dest='stabilize_tripod', action='store_true',
                        default=STABILIZE_TRIPOD,
                        help='Use tripod mode when stabilizing (default).')
    parser.add_argument('--no-stabilize-tripod', dest='stabilize_tripod', action='store_false',
                        help='Disable tripod mode during stabilization.')
    parser.add_argument('--stabilize-crop-shared', dest='stabilize_crop', action='store_true',
                        default=STABILIZE_CROP_SHARED,
                        help='Crop to the shared frame area after stabilization (default).')
    parser.add_argument('--no-stabilize-crop', dest='stabilize_crop', action='store_false',
                        help='Skip cropping after stabilization to keep edges.')
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

    output_path = args.output_video or OUTPUT_VIDEO
    if not output_path:
        output_path = os.path.join(image_directory, 'output_video.mp4')

    frame_rate = args.frame_rate if args.frame_rate is not None else FRAME_RATE
    frame_duration = args.frame_duration if args.frame_duration is not None else FRAME_DURATION
    if frame_rate:
        frame_duration = None
    elif frame_duration is None:
        frame_duration = 0.1

    extra_args = list(FFMPEG_EXTRA_ARGS)
    if args.extra_args:
        extra_args.extend(args.extra_args)

    stabilization = StabilizationSettings(
        enabled=args.stabilize,
        shakiness=args.stabilize_shakiness,
        accuracy=args.stabilize_accuracy,
        stepsize=args.stabilize_stepsize,
        mincontrast=args.stabilize_mincontrast,
        smoothing=args.stabilize_smoothing,
        tripod=args.stabilize_tripod,
        crop_shared_area=args.stabilize_crop,
    )

    video_settings = VideoSettings(
        enabled=True,
        output_path=output_path,
        ffmpeg_path=args.ffmpeg_path or FFMPEG_PATH,
        frame_rate=frame_rate,
        frame_duration=frame_duration,
        pixel_format=args.pixel_format or PIXEL_FORMAT,
        extra_args=extra_args,
        stabilization=stabilization,
    )

    try:
        stitch_images_to_video(
            images,
            video_settings.output_path,
            ffmpeg_path=video_settings.ffmpeg_path,
            frame_rate=video_settings.frame_rate,
            frame_duration=video_settings.frame_duration,
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
