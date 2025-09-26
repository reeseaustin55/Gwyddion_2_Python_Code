#!/usr/bin/env python
"""Example script demonstrating the batch processing API."""

from __future__ import absolute_import, print_function

import logging
import os

from gwyddion_batch import (
    BatchConfig,
    GwyddionBatchProcessor,
    VideoSettings,
    StabilizationSettings,
    import_gwyddion,
)

# USER SETTINGS - MODIFY THESE VALUES ---------------------------------------
FOLDER_PATH = r'D:\AFM Images\hopg_CORROSION_100mMHClO4_N2flow_Irtip_09162025\2nd attempt\Set1'
CHANNEL_NUMBER = 0
PIXEL_COUNT = 1024
FILE_FILTER = None  # e.g. '.spm'
ADDITIONAL_GWY_PATHS = [r"C:\\Program Files (x86)\\Gwyddion\\bin"]
OUTPUT_SUBDIR = 'processed'

# Video rendering settings -------------------------------------------------
VIDEO_ENABLED = False
VIDEO_OUTPUT = None  # Optional explicit filename
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg-2025-02-24-git-6232f416b1-full_build\\bin\\ffmpeg.exe"  # Or just 'ffmpeg' if on PATH
FRAME_DURATION = 0.1  # seconds
FRAME_RATE = None  # Optional alternative to FRAME_DURATION
PIXEL_FORMAT = 'yuv420p'
FFMPEG_EXTRA_ARGS = []  # e.g. ['-vf', 'scale=ceil(iw/2)*2:ceil(ih/2)*2']
# Stabilization settings ----------------------------------------------------
STABILIZE_VIDEO = False
STABILIZE_SHAKINESS = 5
STABILIZE_ACCURACY = 9
STABILIZE_STEPSIZE = 6
STABILIZE_MINCONTRAST = 0.3
STABILIZE_SMOOTHING = 15
STABILIZE_TRIPOD = True
STABILIZE_CROP_SHARED = True
# ---------------------------------------------------------------------------


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    stabilization_settings = StabilizationSettings(
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
        enabled=VIDEO_ENABLED,
        output_path=VIDEO_OUTPUT,
        ffmpeg_path=FFMPEG_PATH,
        frame_rate=FRAME_RATE,
        frame_duration=FRAME_DURATION,
        pixel_format=PIXEL_FORMAT,
        extra_args=FFMPEG_EXTRA_ARGS,
        stabilization=stabilization_settings,
    )

    config = BatchConfig(
        folder_path=FOLDER_PATH,
        channel_number=CHANNEL_NUMBER,
        pixel_count=PIXEL_COUNT,
        file_filter=FILE_FILTER,
        gwyddion_paths=ADDITIONAL_GWY_PATHS,
        output_directory=os.path.join(FOLDER_PATH, OUTPUT_SUBDIR),
        video_settings=video_settings,
    )

    gwy = import_gwyddion(config.gwyddion_paths, logger=logging.getLogger(__name__))
    processor = GwyddionBatchProcessor(gwy)
    result = processor.process_folder(config)

    logging.info('Finished with %d/%d successes', result['processed'], result['total'])
    if result.get('output_directory'):
        logging.info('Processed images saved to %s', result['output_directory'])
    if result.get('video_path'):
        logging.info('Video written to %s', result['video_path'])


if __name__ == '__main__':
    main()
