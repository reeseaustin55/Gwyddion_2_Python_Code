"""Core functionality for batch processing Gwyddion data files."""

from __future__ import absolute_import

import logging
import os
from contextlib import contextmanager

from .compat import to_native_path
from .video import stitch_images_to_video


SUPPORTED_EXTENSIONS = [
    '.spm', '.afm', '.gwy', '.nanoscope', '.jpk', '.ibw', '.pfc',
    '.dm3', '.dm4', '.mtrx', '.dat', '.txt'
]


def get_supported_extensions():
    """Return a copy of the supported file extensions list."""
    return list(SUPPORTED_EXTENSIONS)


def generate_output_path(input_path, pixel_count, width_nm, channel_number=0,
                         output_directory=None):
    """Generate the output PNG file path for ``input_path``."""
    import os

    directory = output_directory or os.path.dirname(input_path)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    width_nm_int = int(round(width_nm))
    file_name = '%dpx_%dnm_channel%d_%s.png' % (
        pixel_count,
        width_nm_int,
        int(channel_number),
        base_name,
    )
    return os.path.join(directory, file_name)


class GwyddionBatchProcessor(object):
    """Process SPM/AFM files using the Gwyddion Python bindings."""

    def __init__(self, gwy_module, logger=None):
        self.gwy = gwy_module
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @contextmanager
    def _open_container(self, file_path):
        """Context manager yielding a Gwyddion data container."""
        native_path = to_native_path(file_path)
        container = self.gwy.gwy_file_load(native_path, self.gwy.RUN_NONINTERACTIVE)
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

        output_directory = config.ensure_output_directory()
        self.logger.info('Processed images will be written to %s', output_directory)

        self.logger.info('Found %d files to process', len(files))
        self.logger.info('Settings: channels=%s, pixels=%d',
                         ','.join(str(c) for c in config.channel_numbers),
                         config.pixel_count)

        overall_processed = 0
        overall_total = len(files) * len(config.channel_numbers)
        per_channel = {}
        video_paths = {}
        interactive_pending = True

        for channel_number in config.channel_numbers:
            self.logger.info('Processing channel %d', channel_number)
            successes = 0
            output_paths = []
            for path in files:
                interactive = interactive_pending
                result = self.process_file(
                    path,
                    channel_number,
                    config.pixel_count,
                    interactive,
                    output_directory,
                )
                if interactive_pending:
                    interactive_pending = False
                if result:
                    successes += 1
                    output_paths.append(result)

            self.logger.info('Channel %d complete: %d/%d files succeeded',
                             channel_number, successes, len(files))
            per_channel[channel_number] = {
                'processed': successes,
                'total': len(files),
                'output_paths': output_paths,
            }
            overall_processed += successes

            if config.video.enabled and output_paths:
                try:
                    video_path = self._render_video(output_paths, config, channel_number)
                    video_paths[channel_number] = video_path
                    self.logger.info('Channel %d video written to %s',
                                     channel_number, video_path)
                except Exception as exc:
                    self.logger.error('Failed to create video for channel %d: %s',
                                      channel_number, exc)
                    self.logger.debug('Video rendering error details', exc_info=True)

        return {
            'processed': overall_processed,
            'total': overall_total,
            'per_channel': per_channel,
            'output_directory': output_directory,
            'video_paths': video_paths,
        }

    def process_file(self, file_path, channel_number, pixel_count, interactive,
                     output_directory=None):
        """Process a single file and return the output image path."""
        self.logger.info('Processing %s', file_path)
        try:
            with self._open_container(file_path) as container:
                return self._process_container(
                    container,
                    file_path,
                    channel_number,
                    pixel_count,
                    interactive,
                    output_directory,
                )
        except Exception as exc:
            self.logger.error('Error processing %s: %s', file_path, exc)
            self.logger.debug('Detailed error', exc_info=True)
            return False

    # --- Internal helpers -------------------------------------------------

    def _process_container(self, container, file_path, channel_number, pixel_count,
                           interactive, output_directory):
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

        output_path = generate_output_path(
            file_path,
            pixel_count,
            xreal * 1e9,
            channel_number,
            output_directory=output_directory,
        )
        self._save_container(container, output_path, interactive)
        return output_path

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
        directory = os.path.dirname(output_path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        self.logger.info('Saving to %s', output_path)
        run_mode = gwy.RUN_INTERACTIVE if interactive else gwy.RUN_NONINTERACTIVE
        gwy.gwy_file_save(container, to_native_path(output_path), run_mode)

    def _log_resolution_details(self, xres, yres, xreal, yreal):
        self.logger.info('Original resolution: %dx%d pixels', xres, yres)
        self.logger.info('Original size: %.2f x %.2f nm', xreal * 1e9, yreal * 1e9)
        nm_per_pixel_x = (xreal * 1e9) / float(xres)
        nm_per_pixel_y = (yreal * 1e9) / float(yres)
        self.logger.info('Original nm/pixel: %.3f x %.3f', nm_per_pixel_x, nm_per_pixel_y)

    def _render_video(self, image_paths, config, channel_number):
        video_path = config.get_video_output_path(channel_number)
        if not video_path:
            return None
        settings = config.video
        if settings.frame_rate and settings.frame_rate > 0:
            frame_rate = settings.frame_rate
        else:
            frame_rate = None
        frame_duration = settings.frame_duration if frame_rate is None else None
        stitch_images_to_video(
            image_paths,
            video_path,
            ffmpeg_path=settings.ffmpeg_path,
            frame_rate=frame_rate,
            frame_duration=frame_duration,
            pixel_format=settings.pixel_format,
            extra_args=settings.extra_args,
            logger=self.logger,
            stabilization=settings.stabilization,
        )
        return video_path
