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
    ProcessingOptions,
    import_gwyddion,
)

# USER SETTINGS - MODIFY THESE VALUES ---------------------------------------
FOLDER_PATH = r'D:\AFM Images\hopg_CORROSION_100mMHClO4_N2flow_Irtip_09162025\2nd attempt\Set1'
CHANNEL_NUMBERS = [0]
PIXEL_COUNT = 1024
FILE_FILTER = '.ibw'
# The loader automatically searches common install locations such as
# ``C:\\Program Files (x86)\\Gwyddion\\bin`` so extra paths are rarely needed.
ADDITIONAL_GWY_PATHS = []
OUTPUT_SUBDIR = None  # Use the timestamped ``output_YYYYMMDD_HHMMSS`` default when ``None``

# Video rendering settings -------------------------------------------------
VIDEO_ENABLED = False
FFMPEG_PATH = r"C:\\Program Files\\ffmpeg-2025-02-24-git-6232f416b1-full_build\\bin\\ffmpeg.exe"  # Or just 'ffmpeg' if on PATH
VIDEO_DURATION = 10.0  # seconds
# When enabled, render separate UP/DOWN videos using alternating frames
VIDEO_SPLIT_SCANS = False
# Pixel format is fixed to yuv420p by default in the helper
# Additional ffmpeg arguments can be provided via the API if needed
# Stabilization settings ----------------------------------------------------
STABILIZE_VIDEO = False
STABILIZE_SHAKINESS = 5
STABILIZE_ACCURACY = 9
STABILIZE_STEPSIZE = 6
STABILIZE_MINCONTRAST = 0.3
STABILIZE_SMOOTHING = 15
STABILIZE_TRIPOD = True
STABILIZE_CROP_SHARED = True
# Image processing toggles --------------------------------------------------
FLATTENING_ENABLED = True
ALIGN_ROWS_ENABLED = True
ALIGN_METHOD = 'polynomial'  # 'median' or 'polynomial'
ALIGN_DEGREE = 2
REMOVE_SCARS = False
FIX_ZERO = True
EXPORT_STATS = False
GENERATE_ACF = False
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
        ffmpeg_path=FFMPEG_PATH,
        duration_seconds=VIDEO_DURATION,
        stabilization=stabilization_settings,
        frame_rate=None,
        split_scans=VIDEO_SPLIT_SCANS,
    )

    processing_options = ProcessingOptions(
        flatten=FLATTENING_ENABLED,
        align_rows=ALIGN_ROWS_ENABLED,
        align_method=ALIGN_METHOD,
        align_degree=ALIGN_DEGREE,
        remove_scars=REMOVE_SCARS,
        fix_zero=FIX_ZERO,
        export_stats=EXPORT_STATS,
        generate_acf=GENERATE_ACF,
    )

    config = BatchConfig(
        folder_path=FOLDER_PATH,
        channel_numbers=CHANNEL_NUMBERS,
        pixel_count=PIXEL_COUNT,
        file_filter=FILE_FILTER,
        gwyddion_paths=ADDITIONAL_GWY_PATHS,
        output_directory=(os.path.join(FOLDER_PATH, OUTPUT_SUBDIR)
                          if OUTPUT_SUBDIR else None),
        video_settings=video_settings,
        processing_options=processing_options,
    )

    gwy = import_gwyddion(config.gwyddion_paths, logger=logging.getLogger(__name__))
    processor = GwyddionBatchProcessor(gwy)
    result = processor.process_folder(config)

    logging.info('Finished with %d/%d successes', result['processed'], result['total'])
    if result.get('output_directory'):
        logging.info('Processed images saved to %s', result['output_directory'])
    for channel, details in sorted(result.get('per_channel', {}).items()):
        logging.info('Channel %d: %d/%d images saved',
                     channel, details.get('processed', 0), details.get('total', 0))
    for channel, videos in sorted(result.get('video_paths', {}).items()):
        if isinstance(videos, dict):
            for kind, entries in sorted(videos.items()):
                if isinstance(entries, dict):
                    for direction, path in sorted(entries.items()):
                        logging.info('Channel %d %s %s video: %s',
                                     channel, kind, direction, path)
                else:
                    logging.info('Channel %d %s video: %s', channel, kind, entries)
        else:
            logging.info('Channel %d video written to %s', channel, videos)


if __name__ == '__main__':
    main()
