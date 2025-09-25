#!/usr/bin/env python
"""Example script demonstrating the batch processing API."""

from __future__ import absolute_import, print_function

import logging

from gwyddion_batch import BatchConfig, GwyddionBatchProcessor, import_gwyddion

# USER SETTINGS - MODIFY THESE VALUES ---------------------------------------
FOLDER_PATH = r'D:\AFM Images\hopg_CORROSION_100mMHClO4_N2flow_Irtip_09162025\2nd attempt\Set1'
CHANNEL_NUMBER = 0
PIXEL_COUNT = 1024
FILE_FILTER = None  # e.g. '.spm'
ADDITIONAL_GWY_PATHS = [r"C:\\Program Files (x86)\\Gwyddion\\bin"]
# ---------------------------------------------------------------------------


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    config = BatchConfig(
        folder_path=FOLDER_PATH,
        channel_number=CHANNEL_NUMBER,
        pixel_count=PIXEL_COUNT,
        file_filter=FILE_FILTER,
        gwyddion_paths=ADDITIONAL_GWY_PATHS,
    )

    gwy = import_gwyddion(config.gwyddion_paths, logger=logging.getLogger(__name__))
    processor = GwyddionBatchProcessor(gwy)
    result = processor.process_folder(config)

    logging.info('Finished with %d/%d successes', result['processed'], result['total'])


if __name__ == '__main__':
    main()
