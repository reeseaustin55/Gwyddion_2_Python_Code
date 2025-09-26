from __future__ import absolute_import, print_function

import argparse
import logging
import sys

from .config import (BatchConfig, VideoSettings, StabilizationSettings,
                     ProcessingOptions)
from .gwyddion_loader import import_gwyddion
from .processor import GwyddionBatchProcessor

DEFAULT_FILE_FILTER = '.ibw'
DEFAULT_VIDEO_DURATION = 10.0


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description='Batch process AFM/SPM images with Gwyddion.'
    )
    parser.add_argument('folder', help='Folder containing the data files to process.')
    parser.add_argument('--channel', '--channels', dest='channels', action='append',
                        type=int, default=None,
                        help='Channel number to process (default: 0). May be provided multiple times.')
    parser.add_argument('--pixels', type=int, default=1024,
                        help='Target pixel count for scaling (default: 1024).')
    parser.add_argument('--filter', dest='file_filter', default=DEFAULT_FILE_FILTER,
                        help='File extension filter (default: .ibw). Use "all" to process every supported file.')
    parser.add_argument('--gwyddion-path', dest='gwyddion_paths', action='append', default=[],
                        help='Additional directories to search for the Gwyddion Python bindings.')
    parser.add_argument('--log-level', default='INFO',
                        help='Logging level (DEBUG, INFO, WARNING, ...). Default: INFO.')
    parser.add_argument('--output-dir', dest='output_directory', default=None,
                        help='Optional directory for processed images. Defaults to a timestamped subfolder.')
    parser.add_argument('--video', dest='video', action='store_true',
                        help='Enable stitching processed images into a video.')
    parser.add_argument('--video-duration', dest='video_duration', type=float,
                        default=DEFAULT_VIDEO_DURATION,
                        help='Length of the rendered video in seconds (default: 10).')
    parser.add_argument('--video-fps', dest='video_fps', type=float,
                        default=None,
                        help='Constant frame rate for rendered videos (default: 30). Use 0 to keep per-frame durations.')
    parser.add_argument('--ffmpeg', dest='ffmpeg_path', default='ffmpeg',
                        help='Path to the ffmpeg executable (default: ffmpeg).')
    parser.add_argument('--pixel-format', dest='pixel_format', default='yuv420p',
                        help='Pixel format for ffmpeg output (default: yuv420p).')
    parser.add_argument('--stabilize', dest='stabilize', action='store_true',
                        help='Enable drift correction and cropping when rendering the video.')
    parser.add_argument('--no-stabilize', dest='stabilize', action='store_false',
                        help='Disable video stabilization (default).')
    parser.add_argument('--split-scans', dest='split_scans', action='store_true', default=False,
                        help='Render separate videos for alternating frames (UP/DOWN scans).')
    parser.add_argument('--no-split-scans', dest='split_scans', action='store_false',
                        help='Render a single video per channel (default).')
    parser.add_argument('--no-crop-shared', dest='stabilize_crop', action='store_false', default=True,
                        help='Skip cropping the stabilized video to the shared frame area.')
    parser.add_argument('--crop-shared', dest='stabilize_crop', action='store_true',
                        help='Crop the stabilized video to the shared frame area (default).')
    parser.set_defaults(stabilize=False)
    parser.add_argument('--no-flatten', dest='flatten', action='store_false', default=True,
                        help='Disable the flattening (level) step.')
    parser.add_argument('--flatten', dest='flatten', action='store_true',
                        help='Enable the flattening (level) step (default).')
    parser.add_argument('--no-align-rows', dest='align_rows', action='store_false', default=True,
                        help='Skip row alignment processing.')
    parser.add_argument('--align-rows', dest='align_rows', action='store_true',
                        help='Enable row alignment processing (default).')
    parser.add_argument('--align-method', dest='align_method', choices=['median', 'polynomial'],
                        default='polynomial', help='Alignment method: median (2) or polynomial (0).')
    parser.add_argument('--align-degree', dest='align_degree', type=int, default=2,
                        help='Polynomial degree when using polynomial row alignment (default: 2).')
    parser.add_argument('--remove-scars', dest='remove_scars', action='store_true', default=False,
                        help='Enable scar removal on processed images.')
    parser.add_argument('--no-remove-scars', dest='remove_scars', action='store_false',
                        help='Disable scar removal (default).')
    parser.add_argument('--no-fix-zero', dest='fix_zero', action='store_false', default=True,
                        help='Skip zero fixing even for height channels.')
    parser.add_argument('--fix-zero', dest='fix_zero', action='store_true',
                        help='Enable zero fixing on height channels (default).')
    parser.add_argument('--stats', dest='export_stats', action='store_true', default=False,
                        help='Export per-image statistics alongside the PNG output.')
    parser.add_argument('--no-stats', dest='export_stats', action='store_false',
                        help='Disable statistics export (default).')
    parser.add_argument('--acf', dest='generate_acf', action='store_true', default=False,
                        help='Generate and export an autocorrelation image for each processed channel.')
    parser.add_argument('--no-acf', dest='generate_acf', action='store_false',
                        help='Disable autocorrelation export (default).')
    parser.set_defaults(
        flatten=True,
        align_rows=True,
        remove_scars=False,
        fix_zero=True,
        export_stats=False,
        generate_acf=False,
    )
    return parser


def configure_logging(level_name):
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format='%(levelname)s: %(message)s')
    return logging.getLogger(__name__)


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    logger = logging.getLogger(__name__)

    try:
        file_filter = args.file_filter
        if file_filter and file_filter.lower() == 'all':
            file_filter = None
        stabilization_settings = StabilizationSettings(
            enabled=args.stabilize,
            shakiness=5,
            accuracy=9,
            stepsize=6,
            mincontrast=0.3,
            smoothing=15,
            tripod=True,
            crop_shared_area=args.stabilize_crop,
        )
        video_duration = args.video_duration if args.video else None
        video_frame_rate = args.video_fps if (args.video and args.video_fps is not None) else None
        video_settings = VideoSettings(
            enabled=args.video,
            ffmpeg_path=args.ffmpeg_path,
            duration_seconds=video_duration,
            pixel_format=args.pixel_format,
            stabilization=stabilization_settings,
            frame_rate=video_frame_rate,
            split_scans=args.split_scans,
        )
        channel_numbers = args.channels or [0]
        processing_options = ProcessingOptions(
            flatten=args.flatten,
            align_rows=args.align_rows,
            align_method=args.align_method,
            align_degree=args.align_degree,
            remove_scars=args.remove_scars,
            fix_zero=args.fix_zero,
            export_stats=args.export_stats,
            generate_acf=args.generate_acf,
        )
        config = BatchConfig(
            folder_path=args.folder,
            channel_numbers=channel_numbers,
            pixel_count=args.pixels,
            file_filter=file_filter,
            gwyddion_paths=args.gwyddion_paths,
            output_directory=args.output_directory,
            video_settings=video_settings,
            processing_options=processing_options,
        )
        gwy = import_gwyddion(config.gwyddion_paths, logger=logger)
    except Exception as exc:
        logger.error(str(exc))
        return 1

    processor = GwyddionBatchProcessor(gwy)
    result = processor.process_folder(config)
    return 0 if result['processed'] == result['total'] else 1


if __name__ == '__main__':  # pragma: no cover - CLI entry point
    sys.exit(main())
