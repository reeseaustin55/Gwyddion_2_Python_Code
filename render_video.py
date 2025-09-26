#!/usr/bin/env python
"""Utility script to stitch processed images into a stabilized video."""

from __future__ import absolute_import, print_function

import glob
import logging
import os

from gwyddion_batch import (
    StabilizationSettings,
    VideoSettings,
    stitch_images_to_video,
)

# USER SETTINGS - MODIFY AS NEEDED -------------------------------------------
IMAGE_DIRECTORY = r'D:\\AFM Images\\Video Processing\\processed'
IMAGE_PATTERN = '*.png'
OUTPUT_VIDEO = None  # Defaults to <IMAGE_DIRECTORY>/output_video.mp4 when None
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


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    logger = logging.getLogger(__name__)

    if not IMAGE_DIRECTORY:
        logger.error('IMAGE_DIRECTORY must be configured')
        return

    image_directory = os.path.abspath(IMAGE_DIRECTORY)
    if not os.path.isdir(image_directory):
        logger.error('Image directory %s does not exist or is not a directory', image_directory)
        return

    images = collect_images(image_directory, IMAGE_PATTERN)
    if not images:
        logger.error('No images matching %s found in %s', IMAGE_PATTERN, image_directory)
        return

    logger.info('Found %d images to stitch', len(images))

    output_path = OUTPUT_VIDEO
    if not output_path:
        output_path = os.path.join(image_directory, 'output_video.mp4')

    frame_rate = FRAME_RATE
    frame_duration = FRAME_DURATION
    if frame_rate:
        frame_duration = None
    elif frame_duration is None:
        frame_duration = 0.1

    stabilization = StabilizationSettings(
        enabled=STABILIZE_VIDEO,
        shakiness=STABILIZE_SHAKINESS,
        accuracy=STABILIZE_ACCURACY,
        stepsize=STABILIZE_STEPSIZE,
        mincontrast=STABILIZE_MINCONTRAST,
        smoothing=STABILIZE_SMOOTHING,
        tripod=STABILIZE_TRIPOD,
        crop_shared_area=STABILIZE_CROP_SHARED,
    )

    video_settings = VideoSettings(
        enabled=True,
        output_path=output_path,
        ffmpeg_path=FFMPEG_PATH,
        frame_rate=frame_rate,
        frame_duration=frame_duration,
        pixel_format=PIXEL_FORMAT,
        extra_args=FFMPEG_EXTRA_ARGS,
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
    except Exception as exc:
        logger.error('Failed to render video: %s', exc)
        raise
    else:
        logger.info('Video written to %s', video_settings.output_path)


if __name__ == '__main__':
    main()
