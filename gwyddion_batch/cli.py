"""Command line interface for the Gwyddion batch processor."""

from __future__ import absolute_import, print_function

import argparse
import logging
import sys

from .config import BatchConfig
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
        config = BatchConfig(
            folder_path=args.folder,
            channel_number=args.channel,
            pixel_count=args.pixels,
            file_filter=args.file_filter,
            gwyddion_paths=args.gwyddion_paths,
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
