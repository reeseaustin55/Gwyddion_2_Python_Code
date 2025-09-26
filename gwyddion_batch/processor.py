"""Core functionality for batch processing Gwyddion data files."""

from __future__ import absolute_import

import logging
import os
from contextlib import contextmanager

from .compat import to_native_path
from .config import format_time_multiplier, ProcessingOptions
from .video import stitch_images_to_video


SUPPORTED_EXTENSIONS = [
    '.spm', '.afm', '.gwy', '.nanoscope', '.jpk', '.ibw', '.pfc',
    '.dm3', '.dm4', '.mtrx', '.dat', '.txt'
]


def get_supported_extensions():
    """Return a copy of the supported file extensions list."""
    return list(SUPPORTED_EXTENSIONS)


def build_output_basename(input_path, pixel_count, width_nm, channel_number=0):
    """Return the base filename (without extension) for processed outputs."""
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    width_nm_int = int(round(width_nm))
    return '%dpx_%dnm_channel%d_%s' % (
        int(pixel_count),
        width_nm_int,
        int(channel_number),
        base_name,
    )


def generate_output_path(input_path, pixel_count, width_nm, channel_number=0,
                         output_directory=None):
    """Generate the output PNG file path for ``input_path``."""
    import os

    directory = output_directory or os.path.dirname(input_path)
    file_name = build_output_basename(input_path, pixel_count, width_nm, channel_number) + '.png'
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

        file_times = {}
        for path in files:
            try:
                file_times[path] = os.path.getmtime(path)
            except OSError:
                file_times[path] = None

        self.logger.info('Found %d files to process', len(files))
        self.logger.info('Settings: channels=%s, pixels=%d',
                         ','.join(str(c) for c in config.channel_numbers),
                         config.pixel_count)

        overall_processed = 0
        overall_total = len(files) * len(config.channel_numbers)
        per_channel = {}
        video_paths = {}
        interactive_pending = True

        processing_options = getattr(config, 'processing', None)

        for channel_number in config.channel_numbers:
            self.logger.info('Processing channel %d', channel_number)
            successes = 0
            output_paths = []
            capture_times = []
            for path in files:
                interactive = interactive_pending
                result = self.process_file(
                    path,
                    channel_number,
                    config.pixel_count,
                    interactive,
                    output_directory,
                    options=processing_options,
                )
                if interactive_pending:
                    interactive_pending = False
                if result:
                    successes += 1
                    output_paths.append(result)
                    capture_times.append(file_times.get(path))

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
                    video_path = self._render_video(
                        output_paths,
                        capture_times,
                        config,
                        channel_number,
                    )
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
                     output_directory=None, options=None):
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
                    options,
                )
        except Exception as exc:
            self.logger.error('Error processing %s: %s', file_path, exc)
            self.logger.debug('Detailed error', exc_info=True)
            return False

    # --- Internal helpers -------------------------------------------------

    def _process_container(self, container, file_path, channel_number, pixel_count,
                           interactive, output_directory, options):
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
        if options is None:
            options = ProcessingOptions()
        self._apply_pre_scaling_steps(container, settings, options)

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
        self._apply_post_scaling_steps(container, settings, options)

        processed_field = gwy.gwy_app_data_browser_get_current(gwy.APP_DATA_FIELD)

        output_path = generate_output_path(
            file_path,
            pixel_count,
            xreal * 1e9,
            channel_number,
            output_directory=output_directory,
        )
        self._save_container(container, output_path, interactive)

        if options.export_stats:
            self._write_statistics(processed_field, output_path)
        if options.generate_acf:
            self._generate_acf_image(container, settings, output_path, scaled_channel)
        return output_path

    def _apply_pre_scaling_steps(self, container, settings, options):
        gwy = self.gwy
        if options.flatten:
            self.logger.debug('Applying flattening before scaling')
            gwy.gwy_process_func_run('level', container, gwy.RUN_IMMEDIATE)
        if options.align_rows:
            self._run_align_rows(container, settings, options)
        if options.remove_scars:
            self.logger.debug('Removing scars')
            gwy.gwy_process_func_run('remove_scars', container, gwy.RUN_IMMEDIATE)
            if options.align_rows:
                self.logger.debug('Re-aligning rows after scar removal')
                self._run_align_rows(container, settings, options)

    def _apply_scaling(self, container, settings, pixel_count, xres, yres):
        gwy = self.gwy
        self.logger.info('Scaling to %d pixels', pixel_count)
        settings.set_int32_by_name('/module/scale/interp', 4)
        settings.set_boolean_by_name('/module/scale/proportional', True)
        settings.set_double_by_name('/module/scale/ratio', float(pixel_count) / float(xres))
        settings.set_boolean_by_name('/module/scale/proportional', False)
        settings.set_double_by_name('/module/scale/aspectratio', float(xres) / float(yres))
        gwy.gwy_process_func_run('scale', container, gwy.RUN_IMMEDIATE)

    def _apply_post_scaling_steps(self, container, settings, options):
        gwy = self.gwy
        if options.flatten:
            self.logger.debug('Flattening scaled data')
            gwy.gwy_process_func_run('level', container, gwy.RUN_IMMEDIATE)
        if options.align_rows:
            self.logger.debug('Aligning rows on scaled data')
            self._run_align_rows(container, settings, options)
        if options.fix_zero:
            data_field = gwy.gwy_app_data_browser_get_current(gwy.APP_DATA_FIELD)
            if self._is_height_channel(data_field):
                self.logger.debug('Applying fix-zero to height channel')
                gwy.gwy_process_func_run('fix_zero', container, gwy.RUN_IMMEDIATE)
            else:
                self.logger.debug('Skipping fix-zero for non-height channel')

    def _run_align_rows(self, container, settings, options):
        gwy = self.gwy
        method = 0 if options.align_method == 'polynomial' else 2
        settings.set_int32_by_name('/module/linematch/method', method)
        if method == 0:
            degree = options.align_degree if options.align_degree is not None else 2
            try:
                degree_value = int(degree)
            except Exception:
                degree_value = 2
            if degree_value < 0:
                degree_value = 0
            settings.set_int32_by_name('/module/linematch/degree', degree_value)
        self.logger.debug('Running align_rows with method %d', method)
        gwy.gwy_process_func_run('align_rows', container, gwy.RUN_IMMEDIATE)

    def _is_height_channel(self, data_field):
        if data_field is None:
            return False
        try:
            unit = data_field.get_si_unit_z()
        except AttributeError:
            return True
        if unit is None:
            return True
        representations = []
        for attr in ('get_string', 'get_unit_string', 'get_symbol', 'get_si_string'):
            getter = getattr(unit, attr, None)
            if getter is None:
                continue
            try:
                value = getter()
            except Exception:
                continue
            if value:
                representations.append(value)
        for text in representations:
            normalized = str(text).strip().lower()
            if normalized in ('m', 'nm', 'pm'):
                return True
        return False

    def _write_statistics(self, data_field, output_path):
        stats = self._collect_statistics(data_field)
        if not stats:
            return None
        base, _ = os.path.splitext(output_path)
        stats_path = base + '_stats.txt'
        try:
            directory = os.path.dirname(stats_path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            with open(stats_path, 'w') as handle:
                for key in sorted(stats):
                    handle.write('%s: %s\n' % (key, stats[key]))
            self.logger.info('Statistics written to %s', stats_path)
            return stats_path
        except Exception as exc:
            self.logger.error('Failed to write statistics for %s: %s', output_path, exc)
            self.logger.debug('Statistics error details', exc_info=True)
            return None

    def _collect_statistics(self, data_field):
        if data_field is None:
            return {}
        stats = {}
        attribute_map = [
            ('get_min', 'min'),
            ('get_max', 'max'),
            ('get_mean', 'mean'),
            ('get_rms', 'rms'),
            ('get_skew', 'skewness'),
            ('get_kurtosis', 'kurtosis'),
        ]
        for attr_name, label in attribute_map:
            getter = getattr(data_field, attr_name, None)
            if getter is None:
                continue
            try:
                value = getter()
            except Exception:
                continue
            if value is not None:
                stats[label] = '%.6g' % float(value)
        try:
            stats['x_pixels'] = int(data_field.get_xres())
            stats['y_pixels'] = int(data_field.get_yres())
        except Exception:
            pass
        try:
            stats['x_size_nm'] = '%.6g' % (float(data_field.get_xreal()) * 1e9)
            stats['y_size_nm'] = '%.6g' % (float(data_field.get_yreal()) * 1e9)
        except Exception:
            pass
        return stats

    def _generate_acf_image(self, container, settings, output_path, scaled_channel_id):
        gwy = self.gwy
        try:
            settings.set_boolean_by_name('/module/acf2d/create_image', True)
        except Exception:
            try:
                settings.set_int32_by_name('/module/acf2d/create_image', 1)
            except Exception:
                pass
        try:
            gwy.gwy_process_func_run('acf2d', container, gwy.RUN_IMMEDIATE)
            data_ids = gwy.gwy_app_data_browser_get_data_ids(container)
            if not data_ids:
                return None
            acf_channel = data_ids[-1]
            gwy.gwy_app_data_browser_select_data_field(container, acf_channel)
            base, _ = os.path.splitext(output_path)
            acf_path = base + '_acf.png'
            self._save_container(container, acf_path, interactive=False)
            self.logger.info('ACF saved to %s', acf_path)
            try:
                gwy.gwy_app_data_browser_select_data_field(container, scaled_channel_id)
            except Exception:
                pass
            return acf_path
        except Exception as exc:
            self.logger.error('Failed to generate ACF for %s: %s', output_path, exc)
            self.logger.debug('ACF generation error details', exc_info=True)
            return None

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

    def _render_video(self, image_paths, capture_times, config, channel_number):
        settings = config.video

        frame_count = len(image_paths)
        actual_duration = self._compute_actual_duration(capture_times)
        target_duration = settings.duration_seconds
        if target_duration is None:
            if actual_duration > 0:
                target_duration = actual_duration
            else:
                target_duration = max(frame_count * 0.1, 10.0)
        if target_duration <= 0:
            target_duration = max(frame_count * 0.1, 1.0)

        time_multiplier = None
        if actual_duration > 0 and target_duration > 0:
            time_multiplier = actual_duration / float(target_duration)

        frame_durations = self._build_frame_durations(
            capture_times,
            frame_count,
            target_duration,
            time_multiplier,
        )

        video_path = config.get_video_output_path(channel_number, time_multiplier)
        if not video_path:
            return None

        label = format_time_multiplier(time_multiplier) if time_multiplier else '1X'
        self.logger.info('Channel %d capture span: %.2f s; multiplier %s',
                         channel_number, actual_duration, label)

        stitch_images_to_video(
            image_paths,
            video_path,
            ffmpeg_path=settings.ffmpeg_path,
            frame_durations=frame_durations,
            frame_rate=settings.frame_rate,
            pixel_format=settings.pixel_format,
            extra_args=settings.extra_args,
            logger=self.logger,
            stabilization=settings.stabilization,
        )
        return video_path

    def _compute_actual_duration(self, capture_times):
        first = None
        last = None
        for timestamp in capture_times:
            if timestamp is None:
                continue
            if first is None:
                first = timestamp
            last = timestamp
        if first is None or last is None:
            return 0.0
        if last < first:
            return 0.0
        return float(last - first)

    def _build_frame_durations(self, capture_times, frame_count,
                               target_duration, time_multiplier):
        if frame_count <= 0:
            return []

        fallback = target_duration / float(frame_count)
        if fallback <= 0:
            fallback = 0.1

        if not capture_times:
            return [fallback] * frame_count

        aligned = list(capture_times[:frame_count])
        while len(aligned) < frame_count:
            aligned.append(None)

        intervals = []
        for index in range(frame_count - 1):
            current = aligned[index]
            nxt = aligned[index + 1]
            if current is None or nxt is None or nxt < current:
                intervals.append(None)
            else:
                intervals.append(float(nxt - current))
        if intervals:
            intervals.append(intervals[-1])
        else:
            intervals.append(None)

        durations = []
        for interval in intervals:
            if (time_multiplier and time_multiplier > 0 and interval
                    and interval > 0):
                durations.append(interval / float(time_multiplier))
            else:
                durations.append(fallback)
        if len(durations) > frame_count:
            durations = durations[:frame_count]
        elif len(durations) < frame_count:
            durations.extend([fallback] * (frame_count - len(durations)))
        return durations
