"""Core functionality for batch processing Gwyddion data files."""

from __future__ import absolute_import

import logging
from contextlib import contextmanager


SUPPORTED_EXTENSIONS = [
    '.spm', '.afm', '.gwy', '.nanoscope', '.jpk', '.ibw', '.pfc',
    '.dm3', '.dm4', '.mtrx', '.dat', '.txt'
]


def get_supported_extensions():
    """Return a copy of the supported file extensions list."""
    return list(SUPPORTED_EXTENSIONS)


def generate_output_path(input_path, pixel_count, width_nm):
    """Generate the output PNG file path for ``input_path``."""
    import os

    directory = os.path.dirname(input_path)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    width_nm_int = int(round(width_nm))
    return os.path.join(directory, '%dpx_%dnm_%s.png' % (pixel_count, width_nm_int, base_name))


class GwyddionBatchProcessor(object):
    """Process SPM/AFM files using the Gwyddion Python bindings."""

    def __init__(self, gwy_module, logger=None):
        self.gwy = gwy_module
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @contextmanager
    def _open_container(self, file_path):
        """Context manager yielding a Gwyddion data container."""
        container = self.gwy.gwy_file_load(file_path, self.gwy.RUN_NONINTERACTIVE)
        if not container:
            raise RuntimeError('Failed to load %s' % file_path)
        self.gwy.gwy_app_data_browser_add(container)
        try:
            yield container
        finally:
            self.gwy.gwy_app_data_browser_remove(container)

    def process_folder(self, config):
        """Process all files from ``config`` and return a summary dict."""
        config.validate()
        extensions = config.supported_extensions(get_supported_extensions())
        files = list(config.iter_input_files(extensions))

        if not files:
            self.logger.warning(
                'No supported files found in %s. Supported extensions: %s',
                config.folder_path, ', '.join(extensions)
            )
            return {'processed': 0, 'total': 0}

        self.logger.info('Found %d files to process', len(files))
        self.logger.info('Settings: channel=%d, pixels=%d',
                         config.channel_number, config.pixel_count)

        successes = 0
        for index, path in enumerate(files):
            interactive = (index == 0)
            if self.process_file(path, config.channel_number, config.pixel_count, interactive):
                successes += 1

        self.logger.info('Processing complete: %d/%d files succeeded', successes, len(files))
        return {'processed': successes, 'total': len(files)}

    def process_file(self, file_path, channel_number, pixel_count, interactive):
        """Process a single file, returning ``True`` on success."""
        self.logger.info('Processing %s', file_path)
        try:
            with self._open_container(file_path) as container:
                return self._process_container(
                    container, file_path, channel_number, pixel_count, interactive
                )
        except Exception as exc:
            self.logger.error('Error processing %s: %s', file_path, exc)
            self.logger.debug('Detailed error', exc_info=True)
            return False

    # --- Internal helpers -------------------------------------------------

    def _process_container(self, container, file_path, channel_number, pixel_count, interactive):
        gwy = self.gwy

        data_ids = gwy.gwy_app_data_browser_get_data_ids(container)
        if not data_ids:
            raise RuntimeError('No data channels present')
        if channel_number >= len(data_ids):
            self.logger.warning(
                'Channel %d not found in %s. Falling back to channel 0.',
                channel_number, file_path
            )
            channel_number = 0
        gwy.gwy_app_data_browser_select_data_field(container, data_ids[channel_number])

        settings = gwy.gwy_app_settings_get()
        self._apply_initial_processing(container, settings)

        data_field = gwy.gwy_app_data_browser_get_current(gwy.APP_DATA_FIELD)
        xres = data_field.get_xres()
        yres = data_field.get_yres()
        xreal = data_field.get_xreal()
        yreal = data_field.get_yreal()

        self._log_resolution_details(xres, yres, xreal, yreal)
        self._apply_scaling(container, settings, pixel_count, xres, yres)

        new_y_pixels = max(1, int(round(pixel_count * float(yres) / float(xres))))
        new_nm_per_pixel_x = (xreal * 1e9) / float(pixel_count)
        new_nm_per_pixel_y = (yreal * 1e9) / float(new_y_pixels)
        self.logger.info('Scaled resolution: %dx%d pixels', pixel_count, new_y_pixels)
        self.logger.info('Scaled nm/pixel: %.3f x %.3f', new_nm_per_pixel_x, new_nm_per_pixel_y)

        scaled_channel = gwy.gwy_app_data_browser_get_data_ids(container)[-1]
        gwy.gwy_app_data_browser_select_data_field(container, scaled_channel)
        self._apply_final_alignment(container, settings)

        output_path = generate_output_path(file_path, pixel_count, xreal * 1e9)
        self._save_container(container, output_path, interactive)
        return True

    def _apply_initial_processing(self, container, settings):
        gwy = self.gwy
        self.logger.debug('Applying initial leveling and alignment')
        gwy.gwy_process_func_run('level', container, gwy.RUN_IMMEDIATE)
        settings.set_int32_by_name('/module/linematch/method', 2)
        gwy.gwy_process_func_run('align_rows', container, gwy.RUN_IMMEDIATE)
        gwy.gwy_process_func_run('align_rows', container, gwy.RUN_IMMEDIATE)
        gwy.gwy_process_func_run('level', container, gwy.RUN_IMMEDIATE)
        gwy.gwy_process_func_run('fix_zero', container, gwy.RUN_IMMEDIATE)

    def _apply_scaling(self, container, settings, pixel_count, xres, yres):
        gwy = self.gwy
        self.logger.info('Scaling to %d pixels', pixel_count)
        settings.set_int32_by_name('/module/scale/interp', 4)
        settings.set_boolean_by_name('/module/scale/proportional', True)
        settings.set_double_by_name('/module/scale/ratio', float(pixel_count) / float(xres))
        settings.set_boolean_by_name('/module/scale/proportional', False)
        settings.set_double_by_name('/module/scale/aspectratio', float(xres) / float(yres))
        gwy.gwy_process_func_run('scale', container, gwy.RUN_IMMEDIATE)

    def _apply_final_alignment(self, container, settings):
        gwy = self.gwy
        self.logger.debug('Applying final alignment')
        settings.set_int32_by_name('/module/linematch/method', 0)
        settings.set_int32_by_name('/module/linematch/degree', 2)
        gwy.gwy_process_func_run('align_rows', container, gwy.RUN_IMMEDIATE)

    def _save_container(self, container, output_path, interactive):
        gwy = self.gwy
        self.logger.info('Saving to %s', output_path)
        run_mode = gwy.RUN_INTERACTIVE if interactive else gwy.RUN_NONINTERACTIVE
        gwy.gwy_file_save(container, output_path, run_mode)

    def _log_resolution_details(self, xres, yres, xreal, yreal):
        self.logger.info('Original resolution: %dx%d pixels', xres, yres)
        self.logger.info('Original size: %.2f x %.2f nm', xreal * 1e9, yreal * 1e9)
        nm_per_pixel_x = (xreal * 1e9) / float(xres)
        nm_per_pixel_y = (yreal * 1e9) / float(yres)
        self.logger.info('Original nm/pixel: %.3f x %.3f', nm_per_pixel_x, nm_per_pixel_y)
