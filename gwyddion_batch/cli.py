from __future__ import absolute_import, print_function

import argparse
import logging
import sys

from .config import BatchConfig, VideoSettings, StabilizationSettings
from .gwyddion_loader import import_gwyddion
from .processor import GwyddionBatchProcessor

DEFAULT_FILE_FILTER = '.ibw'
DEFAULT_VIDEO_DURATION = 10.0
DEFAULT_VIDEO_FPS = 30.0


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
                        default=DEFAULT_VIDEO_FPS,
                        help='Target frame rate for rendered videos (default: 30).')
    parser.add_argument('--ffmpeg', dest='ffmpeg_path', default='ffmpeg',
                        help='Path to the ffmpeg executable (default: ffmpeg).')
    parser.add_argument('--pixel-format', dest='pixel_format', default='yuv420p',
                        help='Pixel format for ffmpeg output (default: yuv420p).')
    parser.add_argument('--stabilize', dest='stabilize', action='store_true',
                        help='Enable drift correction and cropping when rendering the video.')
    parser.add_argument('--no-stabilize', dest='stabilize', action='store_false',
                        help='Disable video stabilization (default).')
    parser.add_argument('--no-crop-shared', dest='stabilize_crop', action='store_false', default=True,
                        help='Skip cropping the stabilized video to the shared frame area.')
    parser.add_argument('--crop-shared', dest='stabilize_crop', action='store_true',
                        help='Crop the stabilized video to the shared frame area (default).')
    parser.set_defaults(stabilize=False)
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
        video_frame_rate = args.video_fps if args.video else None
        video_settings = VideoSettings(
            enabled=args.video,
            ffmpeg_path=args.ffmpeg_path,
            duration_seconds=video_duration,
            pixel_format=args.pixel_format,
            stabilization=stabilization_settings,
            frame_rate=video_frame_rate,
        )
        channel_numbers = args.channels or [0]
        config = BatchConfig(
            folder_path=args.folder,
            channel_numbers=channel_numbers,
            pixel_count=args.pixels,
            file_filter=file_filter,
            gwyddion_paths=args.gwyddion_paths,
            output_directory=args.output_directory,
            video_settings=video_settings,
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
