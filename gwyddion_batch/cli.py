"""Command line interface for the Gwyddion batch processor."""

from __future__ import absolute_import, print_function

import argparse
import logging
import sys

from .config import BatchConfig, VideoSettings, StabilizationSettings
from .gwyddion_loader import import_gwyddion
from .processor import GwyddionBatchProcessor


def build_arg_parser():
    parser = argparse.ArgumentParser(description='Batch process AFM/SPM images with Gwyddion.')
    parser.add_argument('folder', help='Folder containing the data files to process.')
    parser.add_argument('--channel', type=int, default=0,
                        help='Channel number to process (default: 0).')
    parser.add_argument('--pixels', type=int, default=512,
                        help='Target pixel count for scaling (default: 512).')
    parser.add_argument('--filter', dest='file_filter', default=None,
                        help='Optional file extension filter (e.g. .spm).')
    parser.add_argument('--gwyddion-path', dest='gwyddion_paths', action='append', default=[],
                        help='Additional directories to search for the Gwyddion Python bindings.')
    parser.add_argument('--log-level', default='INFO',
                        help='Logging level (DEBUG, INFO, WARNING, ...). Default: INFO.')
    parser.add_argument('--output-dir', dest='output_directory', default=None,
                        help='Directory where processed images should be stored. '
                             'Defaults to <folder>/processed.')
    parser.add_argument('--video', dest='video', action='store_true',
                        help='Enable stitching processed images into a video.')
    parser.add_argument('--video-output', dest='video_output', default=None,
                        help='Optional path for the rendered video file.')
    parser.add_argument('--ffmpeg', dest='ffmpeg_path', default='ffmpeg',
                        help='Path to the ffmpeg executable (default: ffmpeg).')
    parser.add_argument('--frame-duration', dest='frame_duration', type=float, default=None,
                        help='Frame duration in seconds for the video (default: 0.1).')
    parser.add_argument('--frame-rate', dest='frame_rate', type=float, default=None,
                        help='Frame rate for the video; overrides frame duration when set.')
    parser.add_argument('--pixel-format', dest='pixel_format', default='yuv420p',
                        help='Pixel format for ffmpeg output (default: yuv420p).')
    parser.add_argument('--ffmpeg-extra', dest='ffmpeg_extra', action='append', default=[],
                        help='Additional arguments to pass to ffmpeg. May be used multiple times.')
    parser.add_argument('--stabilize', dest='stabilize', action='store_true',
                        help='Enable drift correction and cropping when rendering the video.')
    parser.add_argument('--no-stabilize', dest='stabilize', action='store_false',
                        help='Disable video stabilization (default).')
    parser.add_argument('--stabilize-shakiness', dest='stabilize_shakiness', type=int, default=5,
                        help='Vidstab shakiness parameter (default: 5).')
    parser.add_argument('--stabilize-accuracy', dest='stabilize_accuracy', type=int, default=9,
                        help='Vidstab accuracy parameter (default: 9).')
    parser.add_argument('--stabilize-stepsize', dest='stabilize_stepsize', type=int, default=6,
                        help='Vidstab stepsize parameter (default: 6).')
    parser.add_argument('--stabilize-mincontrast', dest='stabilize_mincontrast', type=float, default=0.3,
                        help='Vidstab minimum contrast threshold (default: 0.3).')
    parser.add_argument('--stabilize-smoothing', dest='stabilize_smoothing', type=int, default=15,
                        help='Vidstab smoothing radius for the transform stage (default: 15).')
    parser.add_argument('--stabilize-tripod', dest='stabilize_tripod', action='store_true',
                        help='Use tripod mode during stabilization (default).')
    parser.add_argument('--no-stabilize-tripod', dest='stabilize_tripod', action='store_false',
                        help='Disable tripod mode for stabilization.')
    parser.add_argument('--stabilize-crop-shared', dest='stabilize_crop', action='store_true',
                        help='Crop the stabilized video to the shared intersection of all frames (default).')
    parser.add_argument('--no-stabilize-crop', dest='stabilize_crop', action='store_false',
                        help='Keep edge artifacts instead of cropping after stabilization.')
    parser.set_defaults(stabilize=False, stabilize_tripod=True, stabilize_crop=True)
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
        frame_duration = args.frame_duration
        if frame_duration is None and args.frame_rate is None and args.video:
            frame_duration = 0.1

        stabilization_settings = StabilizationSettings(
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
            enabled=args.video,
            output_path=args.video_output,
            ffmpeg_path=args.ffmpeg_path,
            frame_rate=args.frame_rate,
            frame_duration=frame_duration,
            pixel_format=args.pixel_format,
            extra_args=args.ffmpeg_extra,
            stabilization=stabilization_settings,
        )

        config = BatchConfig(
            folder_path=args.folder,
            channel_number=args.channel,
            pixel_count=args.pixels,
            file_filter=args.file_filter,
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
