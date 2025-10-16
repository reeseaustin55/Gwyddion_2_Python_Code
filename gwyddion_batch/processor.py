"""Core functionality for batch processing Gwyddion data files."""

from __future__ import absolute_import

import logging
import os
from collections import defaultdict
from contextlib import contextmanager

from .compat import binary_type, text_type, to_native_path
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
        self._unavailable_functions = set()

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
            acf_paths = []
            psdf_paths = []
            capture_times = []
            channel_output_directory = config.ensure_channel_directory(channel_number)
            if processing_options and getattr(processing_options, 'generate_acf', False):
                acf_directory = config.ensure_acf_directory(channel_number)
            else:
                acf_directory = None
            for path in files:
                interactive = interactive_pending
                result = self.process_file(
                    path,
                    channel_number,
                    config.pixel_count,
                    interactive,
                    channel_output_directory,
                    options=processing_options,
                    acf_directory=acf_directory,
                )
                if interactive_pending:
                    interactive_pending = False
                if result:
                    successes += 1
                    image_path = result.get('image_path')
                    if image_path:
                        output_paths.append(image_path)
                    acf_path = result.get('acf_path')
                    if acf_path:
                        acf_paths.append(acf_path)
                    psdf_path = result.get('psdf_path')
                    if psdf_path:
                        psdf_paths.append(psdf_path)
                    capture_times.append(file_times.get(path))

            self.logger.info('Channel %d complete: %d/%d files succeeded',
                             channel_number, successes, len(files))
            per_channel[channel_number] = {
                'processed': successes,
                'total': len(files),
                'output_paths': output_paths,
                'acf_paths': acf_paths,
                'psdf_paths': psdf_paths,
            }
            overall_processed += successes

            if config.video.enabled and output_paths:
                try:
                    rendered = self._render_channel_videos(
                        channel_number,
                        output_paths,
                        acf_paths,
                        capture_times,
                        config,
                    )
                    if rendered:
                        video_paths[channel_number] = rendered
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
                     output_directory=None, options=None, acf_directory=None):
        """Process a single file and return generated artifact paths."""
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
                    acf_directory,
                )
        except Exception as exc:
            self.logger.error('Error processing %s: %s', file_path, exc)
            self.logger.debug('Detailed error', exc_info=True)
            return False

    # --- Internal helpers -------------------------------------------------

    def _process_container(self, container, file_path, channel_number, pixel_count,
                           interactive, output_directory, options, acf_directory):
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

        stats_path = None
        if options.export_stats:
            stats_path = self._write_statistics(container, processed_field,
                                                scaled_channel, output_path)
        acf_path = None
        if options.generate_acf:
            acf_path = self._generate_acf_image(
                container,
                settings,
                output_path,
                scaled_channel,
                acf_directory,
            )
        psdf_path = None
        if getattr(options, 'generate_psdf', False):
            psdf_path = self._generate_psdf_image(
                container,
                settings,
                output_path,
                scaled_channel,
                options,
                pixel_count,
            )
        return {
            'image_path': output_path,
            'acf_path': acf_path,
            'stats_path': stats_path,
            'psdf_path': psdf_path,
        }

    def _run_process_function(self, container, func_name, description=None,
                              required=False):
        """Execute a Gwyddion processing function if available."""
        gwy = self.gwy
        desc = description or func_name
        if func_name in self._unavailable_functions:
            if required:
                raise RuntimeError('Gwyddion function %s is required for %s but '
                                   'has been marked unavailable' % (func_name, desc))
            self.logger.debug('Skipping %s: previously marked unavailable', func_name)
            return False
        exists_func = getattr(gwy, 'gwy_process_func_exists', None)
        available = True
        if callable(exists_func):
            try:
                available = bool(exists_func(func_name))
            except Exception as exc:
                available = True
                self.logger.debug('Could not confirm availability of %s: %s',
                                  func_name, exc)
        if not available:
            message = 'Gwyddion function %s is not available; skipping %s'
            if required:
                raise RuntimeError(message % (func_name, desc))
            if func_name not in self._unavailable_functions:
                self.logger.warning(message, func_name, desc)
                self._unavailable_functions.add(func_name)
            return False
        try:
            gwy.gwy_process_func_run(func_name, container, gwy.RUN_IMMEDIATE)
            return True
        except Exception as exc:
            if required:
                raise RuntimeError('Failed to run %s: %s' % (desc, exc))
            if func_name not in self._unavailable_functions:
                self.logger.warning('Failed to run %s (%s); skipping. Error: %s',
                                    func_name, desc, exc)
                self._unavailable_functions.add(func_name)
            else:
                self.logger.debug('Skipping %s after previous failure: %s',
                                  func_name, exc)
            self.logger.debug('Detailed error when running %s', func_name,
                              exc_info=True)
            return False

    def _apply_pre_scaling_steps(self, container, settings, options):
        if options.flatten:
            self.logger.debug('Applying flattening before scaling')
            self._run_process_function(
                container,
                'level',
                description='flattening',
            )
        if options.align_rows or options.remove_scars:
            if options.align_rows:
                self.logger.debug('Aligning rows before scaling')
            else:
                self.logger.debug('Aligning rows before scaling (required for scar removal)')
            self._run_align_rows(container, settings, options)
        if options.remove_scars:
            self.logger.debug('Removing scars before scaling')
            self._run_process_function(
                container,
                'remove_scars',
                description='scar removal',
            )
            self.logger.debug('Re-aligning rows after scar removal (pre-scaling)')
            self._run_align_rows(container, settings, options)

    def _apply_scaling(self, container, settings, pixel_count, xres, yres):
        gwy = self.gwy
        self.logger.info('Scaling to %d pixels', pixel_count)
        settings.set_int32_by_name('/module/scale/interp', 4)
        settings.set_boolean_by_name('/module/scale/proportional', True)
        settings.set_double_by_name('/module/scale/ratio', float(pixel_count) / float(xres))
        settings.set_boolean_by_name('/module/scale/proportional', False)
        settings.set_double_by_name('/module/scale/aspectratio', float(xres) / float(yres))
        self._run_process_function(
            container,
            'scale',
            description='scaling',
            required=True,
        )

    def _apply_post_scaling_steps(self, container, settings, options):
        gwy = self.gwy
        if options.flatten:
            self.logger.debug('Flattening scaled data')
            self._run_process_function(
                container,
                'level',
                description='flattening',
            )
        if options.align_rows or options.remove_scars:
            if options.align_rows:
                self.logger.debug('Aligning rows on scaled data')
            else:
                self.logger.debug('Aligning rows on scaled data (required for scar removal)')
            self._run_align_rows(container, settings, options)
        if options.remove_scars:
            self.logger.debug('Removing scars after scaling')
            self._run_process_function(
                container,
                'remove_scars',
                description='scar removal',
            )
            self.logger.debug('Re-aligning rows after scar removal (post-scaling)')
            self._run_align_rows(container, settings, options)
        if options.fix_zero:
            data_field = gwy.gwy_app_data_browser_get_current(gwy.APP_DATA_FIELD)
            if self._is_height_channel(data_field):
                self.logger.debug('Applying fix-zero to height channel')
                self._run_process_function(
                    container,
                    'fix_zero',
                    description='fix-zero',
                )
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
        self._run_process_function(
            container,
            'align_rows',
            description='row alignment',
        )

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

    def _write_statistics(self, container, data_field, channel_id, output_path):
        stats = self._collect_statistics(container, data_field, channel_id)
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

    def _collect_statistics(self, container, data_field, channel_id):
        if data_field is None:
            return {}
        stats = self._collect_statistical_quantities(container, channel_id, data_field)
        if stats:
            return stats
        stats = self._collect_gwyddion_stats(data_field)
        if stats:
            return stats
        stats = self._collect_container_stats(container, channel_id)
        if stats:
            return stats
        return self._collect_basic_statistics(data_field)

    def _collect_statistical_quantities(self, container, channel_id, data_field):
        gwy = self.gwy
        try:
            gwy.gwy_app_data_browser_select_data_field(container, channel_id)
        except Exception:
            pass
        ran = self._run_process_function(
            container,
            'statistical_quantities',
            description='statistical quantities',
        )
        if not ran:
            ran = self._run_process_function(
                container,
                'statquant',
                description='statistical quantities',
            )
        if not ran:
            return {}

        stats = self._extract_statistical_quantities_from_container(container, channel_id)
        if stats:
            return stats

        table_stats = self._extract_statistical_quantities_from_table()
        if table_stats:
            return table_stats

        return {}

    def _stat_container_to_mapping(self, container, prefix=None, seen=None):
        if container is None:
            return {}
        if isinstance(container, (list, tuple, dict)):
            return {}
        if seen is None:
            seen = set()
        container_id = id(container)
        if container_id in seen:
            return {}
        seen.add(container_id)
        keys = self._container_keys(container)
        if not keys:
            return {}
        flat_values = {}
        grouped = defaultdict(dict)
        for key in keys:
            text_key = self._stringify_value(key)
            if not text_key:
                continue
            try:
                value = self._container_fetch(container, key)
            except Exception:
                continue
            if value is None:
                continue
            normalized_key = text_key.strip('/')
            if not normalized_key:
                continue
            segments = [self._stringify_value(part) for part in normalized_key.split('/') if part]
            if not segments:
                segments = [normalized_key]
            leaf = segments[-1].lower()
            base_path = '/'.join(segments[:-1])
            if leaf in ('value', 'values', 'val', 'unit', 'units', 'label', 'name', 'text'):
                grouped[base_path][leaf] = value
                continue
            if self._is_container_like(value):
                nested_prefix = '/'.join(segments)
                nested = self._stat_container_to_mapping(value, prefix=nested_prefix, seen=seen)
                for nested_label, nested_value in nested.items():
                    flat_values[nested_label] = nested_value
                continue
            if isinstance(value, (list, tuple)):
                nested = self._decode_statquant_sequence(value)
                if nested:
                    nested_prefix = '/'.join(segments)
                    for nested_label, nested_value in nested.items():
                        combined_label = self._combine_stat_labels(nested_prefix, nested_label)
                        flat_values[combined_label] = nested_value
                    continue
            combined_label = self._combine_stat_labels('/'.join(segments), None)
            if combined_label:
                value_text = self._format_stat_value(value)
                if value_text:
                    flat_values[combined_label] = value_text
        for path, details in grouped.items():
            label_candidate = details.get('label') or details.get('name') or details.get('text')
            combined_label = self._combine_stat_labels(path, label_candidate)
            value_obj = details.get('value')
            if value_obj is None:
                value_obj = details.get('values')
            unit_obj = details.get('unit')
            if unit_obj is None:
                unit_obj = details.get('units')
            value_text = self._format_stat_value(value_obj)
            unit_text = self._format_stat_unit(unit_obj)
            if value_text:
                if unit_text:
                    flat_values[combined_label] = '%s %s' % (value_text, unit_text)
                else:
                    flat_values[combined_label] = value_text
        if prefix:
            prefixed = {}
            for key, value in flat_values.items():
                prefixed[self._combine_stat_labels(prefix, key)] = value
            return prefixed
        return flat_values

    def _combine_stat_labels(self, prefix, label):
        prefix_text = self._stringify_value(prefix) if prefix is not None else ''
        label_text = self._stringify_value(label) if label is not None else ''
        prefix_parts = [part for part in prefix_text.split('/') if part]
        if label_text:
            leaf = label_text
        elif prefix_parts:
            leaf = prefix_parts[-1]
            prefix_parts = prefix_parts[:-1]
        else:
            leaf = ''
        if prefix_parts:
            prefix_display = ' / '.join(prefix_parts)
            if leaf:
                return '%s: %s' % (prefix_display, leaf)
            return prefix_display
        return leaf

    def _is_container_like(self, value):
        if value is None:
            return False
        if isinstance(value, (list, tuple, dict)):
            return False
        for name in ('get_value_by_name', 'get_object_by_name', 'keys_by_name'):
            if getattr(value, name, None) is not None:
                return True
        return False

    def _container_keys(self, container):
        key_sources = [
            ('keys_by_name', ('',)),
            ('keys', ()),
            ('list_keys', ()),
        ]
        for method_name, args in key_sources:
            method = getattr(container, method_name, None)
            if method is None:
                continue
            try:
                result = method(*args)
            except TypeError:
                try:
                    result = method()
                except Exception:
                    continue
            except Exception:
                continue
            if isinstance(result, (list, tuple, set)):
                keys = list(result)
            else:
                try:
                    keys = list(result)
                except Exception:
                    continue
            if keys:
                return keys
        iterator = getattr(container, '__iter__', None)
        if iterator is not None:
            try:
                keys = list(iterator())
                if keys:
                    return keys
            except TypeError:
                try:
                    keys = list(container)
                    if keys:
                        return keys
                except Exception:
                    pass
            except Exception:
                pass
        return []

    def _container_fetch(self, container, key):
        getter_names = [
            'get_value_by_name',
            'get_object_by_name',
            'get_by_name',
            'get',
        ]
        for name in getter_names:
            getter = getattr(container, name, None)
            if getter is None:
                continue
            try:
                return getter(key)
            except Exception:
                continue
        try:
            return container[key]
        except Exception:
            pass
        gwy = getattr(self, 'gwy', None)
        if gwy is not None:
            for func_name in ('gwy_container_get_object_by_name', 'gwy_container_get_value_by_name'):
                func = getattr(gwy, func_name, None)
                if func is None:
                    continue
                try:
                    return func(container, key)
                except Exception:
                    continue
        return None

    def _extract_statistical_quantities_from_container(self, container, channel_id):
        gwy = self.gwy
        getter = getattr(gwy, 'gwy_container_get_object_by_name', None)
        if getter is None:
            return {}

        candidate_keys = [
            '/%d/statistics/statistical_quantities',
            '/%d/statistics/statquant',
            '/%d/data/statistical_quantities',
            '/%d/data/statquant',
            '/%d/statistics/quantities',
            '/module/statistics/statistical_quantities',
            '/module/statistics/statquant',
            '/module/statistical_quantities/results',
            '/module/statistical_quantities',
            '/module/statistical-quantities/results',
            '/module/statistical-quantities',
        ]

        for template in candidate_keys:
            if '%d' in template:
                key = template % int(channel_id)
            else:
                key = template
            try:
                obj = getter(container, key)
            except Exception:
                obj = None
            stats = self._decode_statistical_quantities_object(obj)
            if stats:
                return stats
        return {}

    def _extract_statistical_quantities_from_table(self):
        gwy = self.gwy
        app_table_enum = getattr(gwy, 'APP_TABLE', None)
        browser_getter = getattr(gwy, 'gwy_app_data_browser_get_current', None)
        if browser_getter is None or app_table_enum is None:
            return {}
        table = None
        try:
            table = browser_getter(app_table_enum)
        except TypeError:
            try:
                table = browser_getter()
            except Exception:
                table = None
        except Exception:
            table = None
        if table is None:
            return {}

        length_getters = [
            'get_n_rows',
            'get_rows',
            'get_length',
            'get_size',
        ]
        value_getters = [
            ('get_value', True),
            ('get', True),
            ('get_cell', True),
            ('value', True),
        ]

        count = None
        for name in length_getters:
            getter = getattr(table, name, None)
            if getter is None:
                continue
            try:
                count = int(getter())
                break
            except Exception:
                continue
        if count is None or count <= 0:
            try:
                count = len(table)
            except Exception:
                count = 0
        if count <= 0:
            return {}

        stats = {}
        for row in range(count):
            name_value = None
            value_value = None
            unit_value = None
            for getter_name, expects_index in value_getters:
                getter = getattr(table, getter_name, None)
                if getter is None:
                    continue
                try:
                    if expects_index:
                        result = getter(row)
                    else:
                        result = getter()
                except TypeError:
                    try:
                        result = getter(row, 0)
                    except Exception:
                        continue
                except Exception:
                    continue
                if isinstance(result, (list, tuple)):
                    if len(result) >= 1 and name_value is None:
                        name_value = result[0]
                    if len(result) >= 2 and value_value is None:
                        value_value = result[1]
                    if len(result) >= 3 and unit_value is None:
                        unit_value = result[2]
                    if name_value is not None and value_value is not None:
                        break
                elif name_value is None:
                    name_value = result
                elif value_value is None:
                    value_value = result
                elif unit_value is None:
                    unit_value = result
            label = self._stringify_value(name_value)
            if not label:
                continue
            value_text = self._format_stat_value(value_value)
            unit_text = self._format_stat_unit(unit_value)
            if unit_text:
                stats[label] = '%s %s' % (value_text, unit_text)
            else:
                stats[label] = value_text
        return stats

    def _decode_statistical_quantities_object(self, obj):
        if not obj:
            return {}
        container_mapping = self._stat_container_to_mapping(obj)
        if container_mapping:
            return container_mapping
        mapping = {}
        if isinstance(obj, (list, tuple)):
            mapping = self._decode_statquant_sequence(obj)
        if mapping:
            return mapping
        normalized = self._normalize_stats_object(obj)
        if normalized:
            enriched = {}
            for key, value in normalized.items():
                label = self._stringify_value(key)
                text_value = self._format_stat_value(value)
                if label:
                    enriched[label] = text_value
            if enriched:
                return enriched

        names = self._extract_sequence(obj, 'names')
        values = self._extract_sequence(obj, 'values')
        units = self._extract_sequence(obj, 'units')
        if not names or not values:
            names = self._extract_sequence(obj, 'labels')
        if not names or not values:
            return {}
        stats = {}
        count = min(len(names), len(values))
        for index in range(count):
            label = self._stringify_value(names[index])
            if not label:
                continue
            value_text = self._format_stat_value(values[index])
            unit_text = ''
            if units and index < len(units):
                unit_text = self._format_stat_unit(units[index])
            if unit_text:
                stats[label] = '%s %s' % (value_text, unit_text)
            else:
                stats[label] = value_text
        return stats

    def _decode_statquant_sequence(self, sequence):
        stats = {}
        for entry in sequence:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                label = self._stringify_value(entry[0])
                value_text = self._format_stat_value(entry[1])
                unit_text = ''
                if len(entry) >= 3:
                    unit_text = self._format_stat_unit(entry[2])
                if label:
                    if unit_text:
                        stats[label] = '%s %s' % (value_text, unit_text)
                    else:
                        stats[label] = value_text
        return stats

    def _extract_sequence(self, container_like, key):
        if not container_like:
            return []
        getter_names = [
            'get',
            'get_value_by_name',
            'get_by_name',
        ]
        for name in getter_names:
            getter = getattr(container_like, name, None)
            if getter is None:
                continue
            try:
                value = getter(key)
            except Exception:
                continue
            sequence = self._sequence_from_object(value)
            if sequence is not None:
                return sequence
        gwy_getter = getattr(self.gwy, 'gwy_container_get_object_by_name', None)
        if gwy_getter is not None:
            try:
                value = gwy_getter(container_like, key)
            except Exception:
                value = None
            sequence = self._sequence_from_object(value)
            if sequence is not None:
                return sequence
        return []

    def _sequence_from_object(self, value):
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, text_type):
            return [value]
        if isinstance(value, binary_type):
            try:
                return [value.decode('utf-8')]
            except Exception:
                return [value]
        to_list = getattr(value, 'to_list', None)
        if callable(to_list):
            try:
                data = to_list()
                if isinstance(data, (list, tuple)):
                    return list(data)
            except Exception:
                pass
        get_data = getattr(value, 'get_data', None)
        if callable(get_data):
            try:
                data = get_data()
                if isinstance(data, (list, tuple)):
                    return list(data)
            except Exception:
                pass
        get_array = getattr(value, 'get_array', None)
        if callable(get_array):
            try:
                data = get_array()
                if isinstance(data, (list, tuple)):
                    return list(data)
            except Exception:
                pass
        length_getters = ['__len__', 'get_length', 'get_n', 'get_size']
        fetch_getters = ['__getitem__', 'get', 'value', 'get_value']
        for len_name in length_getters:
            length_fn = getattr(value, len_name, None)
            if length_fn is None:
                continue
            try:
                length = length_fn()
            except TypeError:
                try:
                    length = length_fn(0)
                except Exception:
                    continue
            except Exception:
                continue
            try:
                length = int(length)
            except Exception:
                continue
            if length <= 0 or length > 4096:
                continue
            for fetch_name in fetch_getters:
                fetch = getattr(value, fetch_name, None)
                if fetch is None:
                    continue
                sequence = []
                success = True
                for index in range(length):
                    try:
                        item = fetch(index)
                    except Exception:
                        success = False
                        break
                    sequence.append(item)
                if success:
                    return sequence
        iterator = getattr(value, '__iter__', None)
        if iterator is not None:
            try:
                return list(iterator())
            except TypeError:
                try:
                    return list(value)
                except Exception:
                    pass
            except Exception:
                pass
        return []

    def _stringify_value(self, value):
        if value is None:
            return ''
        if isinstance(value, text_type):
            return value.strip()
        if isinstance(value, binary_type):
            try:
                return value.decode('utf-8').strip()
            except Exception:
                try:
                    return value.decode('latin-1').strip()
                except Exception:
                    return repr(value)
        try:
            return str(value).strip()
        except Exception:
            return ''

    def _format_stat_value(self, value):
        if value is None:
            return ''
        if isinstance(value, (int, float)):
            return ('%.6g' % float(value)).strip()
        try:
            text = str(value).strip()
            if text:
                return text
        except Exception:
            pass
        return ''

    def _format_stat_unit(self, value):
        text = self._stringify_value(value)
        if not text:
            return ''
        normalized = text.lower()
        if normalized in ('-', 'none', 'unitless', 'dimensionless'):
            return ''
        return text

    def _collect_gwyddion_stats(self, data_field):
        getters = ['get_statistics', 'statistics_get', 'get_stats']
        for name in getters:
            getter = getattr(data_field, name, None)
            if getter is None:
                continue
            try:
                stats_obj = getter()
            except TypeError:
                try:
                    stats_obj = getter(None)
                except Exception:
                    continue
            except Exception:
                continue
            converted = self._normalize_stats_object(stats_obj)
            if converted:
                return converted
        gwy = self.gwy
        function_names = [
            'gwy_data_field_statistics_get',
            'gwy_data_field_stats_get',
        ]
        for func_name in function_names:
            func = getattr(gwy, func_name, None)
            if not callable(func):
                continue
            try:
                stats_obj = func(data_field)
            except TypeError:
                try:
                    stats_obj = func(data_field, None)
                except Exception:
                    continue
            except Exception:
                continue
            converted = self._normalize_stats_object(stats_obj)
            if converted:
                return converted
        return {}

    def _collect_container_stats(self, container, channel_id):
        gwy = self.gwy
        ran = self._run_process_function(
            container,
            'stats',
            description='statistics export',
        )
        if not ran:
            self._run_process_function(
                container,
                'statistics',
                description='statistics export',
            )
        getter = getattr(gwy, 'gwy_container_get_object_by_name', None)
        if getter is None:
            return {}
        key_templates = [
            '/%d/stats',
            '/%d/statistics',
            '/%d/data/stats',
            '/%d/data/statistics',
        ]
        for template in key_templates:
            key = template % int(channel_id)
            try:
                obj = getter(container, key)
            except Exception:
                continue
            if not obj:
                continue
            converted = self._normalize_stats_object(obj)
            if converted:
                return converted
        return {}

    def _collect_basic_statistics(self, data_field):
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

    def _normalize_stats_object(self, stats_obj):
        if not stats_obj:
            return {}
        container_mapping = self._stat_container_to_mapping(stats_obj)
        if container_mapping:
            return container_mapping
        if isinstance(stats_obj, dict):
            return self._stringify_stats(stats_obj)
        to_dict = getattr(stats_obj, 'to_dict', None)
        if to_dict:
            try:
                data = to_dict()
                if isinstance(data, dict):
                    return self._stringify_stats(data)
            except Exception:
                pass
        items = getattr(stats_obj, 'items', None)
        if items:
            try:
                return self._stringify_stats(dict(items()))
            except Exception:
                pass
        result = {}
        for name in dir(stats_obj):
            if name.startswith('_'):
                continue
            try:
                value = getattr(stats_obj, name)
            except Exception:
                continue
            if callable(value) or value is None:
                continue
            try:
                text_name = str(name)
            except Exception:
                text_name = name
            if isinstance(value, (int, float)):
                result[text_name] = '%.6g' % float(value)
            else:
                try:
                    result[text_name] = str(value)
                except Exception:
                    continue
        return result

    def _stringify_stats(self, mapping):
        result = {}
        for key, value in mapping.items():
            if value is None:
                continue
            try:
                text_key = str(key)
            except Exception:
                text_key = key
            if isinstance(value, (int, float)):
                result[text_key] = '%.6g' % float(value)
            else:
                try:
                    result[text_key] = str(value)
                except Exception:
                    continue
        return result

    def _generate_acf_image(self, container, settings, output_path, scaled_channel_id,
                            acf_directory):
        gwy = self.gwy
        try:
            settings.set_boolean_by_name('/module/acf2d/create_image', True)
        except Exception:
            try:
                settings.set_int32_by_name('/module/acf2d/create_image', 1)
            except Exception:
                pass
        try:
            ran = self._run_process_function(
                container,
                'acf2d',
                description='ACF generation',
            )
            if not ran:
                return None
            data_ids = gwy.gwy_app_data_browser_get_data_ids(container)
            if not data_ids:
                return None
            acf_channel = data_ids[-1]
            gwy.gwy_app_data_browser_select_data_field(container, acf_channel)
            base, file_name = os.path.split(output_path)
            name, ext = os.path.splitext(file_name)
            if acf_directory:
                directory = acf_directory
            else:
                directory = os.path.join(base, 'acf')
            if not os.path.isdir(directory):
                os.makedirs(directory)
            acf_path = os.path.join(directory, name + '_acf.png')
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

    def _generate_psdf_image(self, container, settings, output_path,
                             scaled_channel_id, options, pixel_count):
        zoom_value = getattr(options, 'psdf_zoom', 4.0)
        try:
            zoom = float(zoom_value)
        except Exception:
            zoom = 4.0
        if zoom <= 0:
            zoom = 4.0
        try:
            settings.set_double_by_name('/module/psdf/zoom', zoom)
        except Exception:
            try:
                settings.set_int32_by_name('/module/psdf/zoom', int(round(zoom)))
            except Exception:
                pass

        try:
            settings.set_int32_by_name('/module/psdf2d/zoom', int(round(zoom)))
        except Exception:
            try:
                settings.set_double_by_name('/module/psdf2d/zoom', zoom)
            except Exception:
                pass
        return self._generate_derived_image(
            container,
            settings,
            output_path,
            scaled_channel_id,
            directory_name='psdf',
            suffix='_psdf.png',
            description='PSDF generation',
            file_label='PSDF image',
            function_names=['psdf', 'psdf2d'],
            settings_paths=['/module/psdf/create_image'],
            channel_transform=lambda channel_id: self._rescale_square_channel(
                container, settings, channel_id, pixel_count),
        )

    def _generate_derived_image(self, container, settings, output_path, scaled_channel_id,
                                directory_name, suffix, description, file_label,
                                function_names, settings_paths=None,
                                channel_transform=None):
        gwy = self.gwy
        try:
            for key in settings_paths or []:
                try:
                    settings.set_boolean_by_name(key, True)
                    continue
                except Exception:
                    pass
                try:
                    settings.set_int32_by_name(key, 1)
                except Exception:
                    continue

            ran = False
            for func_name in function_names:
                if not func_name:
                    continue
                if self._run_process_function(container, func_name, description=description):
                    ran = True
                    break
            if not ran:
                return None

            data_ids = gwy.gwy_app_data_browser_get_data_ids(container)
            if not data_ids:
                return None
            derived_channel = data_ids[-1]
            if channel_transform is not None:
                try:
                    transformed = channel_transform(derived_channel)
                except Exception:
                    transformed = None
                if transformed is not None:
                    derived_channel = transformed
            gwy.gwy_app_data_browser_select_data_field(container, derived_channel)

            base, file_name = os.path.split(output_path)
            name, _ = os.path.splitext(file_name)
            directory = os.path.join(base, directory_name)
            if not os.path.isdir(directory):
                os.makedirs(directory)
            derived_path = os.path.join(directory, name + suffix)
            self._save_container(container, derived_path, interactive=False)
            self.logger.info('%s saved to %s', file_label, derived_path)
            try:
                gwy.gwy_app_data_browser_select_data_field(container, scaled_channel_id)
            except Exception:
                pass
            return derived_path
        except Exception as exc:
            self.logger.error('Failed to generate %s for %s: %s', file_label, output_path, exc)
            self.logger.debug('%s error details', description, exc_info=True)
            return None

    def _rescale_square_channel(self, container, settings, channel_id, pixel_count):
        if pixel_count is None:
            return channel_id
        try:
            target_pixels = int(pixel_count)
        except Exception:
            return channel_id
        if target_pixels <= 0:
            return channel_id

        gwy = self.gwy
        try:
            gwy.gwy_app_data_browser_select_data_field(container, channel_id)
        except Exception:
            return channel_id

        data_field = gwy.gwy_app_data_browser_get_current(gwy.APP_DATA_FIELD)
        if data_field is None:
            return channel_id
        try:
            xres = data_field.get_xres()
            yres = data_field.get_yres()
        except Exception:
            return channel_id
        if xres == target_pixels and yres == target_pixels:
            return channel_id
        if xres <= 0:
            return channel_id

        scale_ratio = float(target_pixels) / float(xres)
        try:
            settings.set_int32_by_name('/module/scale/interp', 1)
        except Exception:
            pass
        try:
            settings.set_boolean_by_name('/module/scale/proportional', True)
        except Exception:
            pass
        try:
            settings.set_double_by_name('/module/scale/ratio', scale_ratio)
        except Exception:
            pass
        try:
            settings.set_boolean_by_name('/module/scale/proportional', False)
        except Exception:
            pass
        try:
            settings.set_double_by_name('/module/scale/aspectratio', 1.0)
        except Exception:
            pass

        if not self._run_process_function(container, 'scale', description='PSDF rescaling'):
            return channel_id

        data_ids = gwy.gwy_app_data_browser_get_data_ids(container)
        if not data_ids:
            return channel_id
        return data_ids[-1]

    def _render_channel_videos(self, channel_number, output_paths, acf_paths,
                               capture_times, config):
        rendered = {}
        base_videos = {}
        base_video = self._render_video(
            output_paths,
            capture_times,
            config,
            channel_number,
            data_kind='base',
            scan_direction='full',
        )
        if base_video:
            base_videos['full'] = base_video
            self.logger.info('Channel %d video written to %s', channel_number, base_video)
        if config.video.split_scans and len(output_paths) > 1:
            up_paths, up_times, down_paths, down_times = self._split_scans(output_paths, capture_times)
            if up_paths:
                up_video = self._render_video(
                    up_paths,
                    up_times,
                    config,
                    channel_number,
                    data_kind='base',
                    scan_direction='up',
                )
                if up_video:
                    base_videos['up'] = up_video
                    self.logger.info('Channel %d up-scan video written to %s',
                                     channel_number, up_video)
            if down_paths:
                down_video = self._render_video(
                    down_paths,
                    down_times,
                    config,
                    channel_number,
                    data_kind='base',
                    scan_direction='down',
                )
                if down_video:
                    base_videos['down'] = down_video
                    self.logger.info('Channel %d down-scan video written to %s',
                                     channel_number, down_video)
        if base_videos:
            rendered['base'] = base_videos

        if acf_paths:
            acf_videos = {}
            acf_video = self._render_video(
                acf_paths,
                capture_times,
                config,
                channel_number,
                data_kind='acf',
                scan_direction='full',
            )
            if acf_video:
                acf_videos['full'] = acf_video
                self.logger.info('Channel %d ACF video written to %s',
                                 channel_number, acf_video)
            if config.video.split_scans and len(acf_paths) > 1:
                up_paths, up_times, down_paths, down_times = self._split_scans(acf_paths, capture_times)
                if up_paths:
                    up_video = self._render_video(
                        up_paths,
                        up_times,
                        config,
                        channel_number,
                        data_kind='acf',
                        scan_direction='up',
                    )
                    if up_video:
                        acf_videos['up'] = up_video
                        self.logger.info('Channel %d ACF up-scan video written to %s',
                                         channel_number, up_video)
                if down_paths:
                    down_video = self._render_video(
                        down_paths,
                        down_times,
                        config,
                        channel_number,
                        data_kind='acf',
                        scan_direction='down',
                    )
                    if down_video:
                        acf_videos['down'] = down_video
                        self.logger.info('Channel %d ACF down-scan video written to %s',
                                         channel_number, down_video)
            if acf_videos:
                rendered['acf'] = acf_videos
        return rendered

    def _split_scans(self, paths, capture_times):
        up_paths = []
        down_paths = []
        up_times = []
        down_times = []
        for index, path in enumerate(paths):
            timestamp = None
            if capture_times and index < len(capture_times):
                timestamp = capture_times[index]
            if index % 2 == 0:
                up_paths.append(path)
                up_times.append(timestamp)
            else:
                down_paths.append(path)
                down_times.append(timestamp)
        return up_paths, up_times, down_paths, down_times

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

    def _render_video(self, image_paths, capture_times, config, channel_number,
                      data_kind='base', scan_direction='full'):
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
            uniform=settings.uniform_frame_duration,
        )

        video_path = config.get_video_output_path(
            channel_number,
            time_multiplier,
            data_kind=data_kind,
            scan_direction=scan_direction,
        )
        if not video_path:
            return None

        label = format_time_multiplier(time_multiplier) if time_multiplier else '1X'
        self.logger.info('Channel %d %s %s capture span: %.2f s; multiplier %s',
                         channel_number, data_kind, scan_direction,
                         actual_duration, label)

        stitch_images_to_video(
            image_paths,
            video_path,
            ffmpeg_path=settings.ffmpeg_path,
            frame_durations=frame_durations,
            frame_duration=(target_duration / float(frame_count)
                            if frame_count else None),
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
                               target_duration, time_multiplier,
                               uniform=False):
        if frame_count <= 0:
            return []

        fallback = target_duration / float(frame_count)
        if fallback <= 0:
            fallback = 0.1

        if uniform:
            return [fallback] * frame_count

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
