"""Configuration helpers for Gwyddion batch processing."""

from __future__ import absolute_import

import datetime
import os


class ProcessingOptions(object):
    """Toggles controlling the per-image Gwyddion processing pipeline."""

    def __init__(self, flatten=True, align_rows=True,
                 align_method='polynomial', align_degree=2,
                 remove_scars=False, fix_zero=True,
                 export_stats=False, generate_acf=False):
        self.flatten = bool(flatten)
        self.align_rows = bool(align_rows)
        method = (align_method or 'polynomial').lower()
        if method not in ('median', 'polynomial'):
            method = 'polynomial'
        self.align_method = method
        try:
            degree_value = int(align_degree)
        except Exception:
            degree_value = 2
        if degree_value < 0:
            degree_value = 0
        self.align_degree = degree_value
        self.remove_scars = bool(remove_scars)
        self.fix_zero = bool(fix_zero)
        self.export_stats = bool(export_stats)
        self.generate_acf = bool(generate_acf)


class StabilizationSettings(object):
    """Settings controlling optional video stabilization."""

    def __init__(self, enabled=False, shakiness=5, accuracy=9, stepsize=6,
                 mincontrast=0.3, smoothing=15, tripod=True,
                 crop_shared_area=True):
        self.enabled = bool(enabled)
        self.shakiness = int(shakiness) if shakiness is not None else 5
        self.accuracy = int(accuracy) if accuracy is not None else 9
        self.stepsize = int(stepsize) if stepsize is not None else 6
        self.mincontrast = float(mincontrast) if mincontrast is not None else 0.3
        self.smoothing = int(smoothing) if smoothing is not None else 15
        self.tripod = bool(tripod)
        self.crop_shared_area = bool(crop_shared_area)


class VideoSettings(object):
    """Configuration for optional video rendering of processed images."""

    def __init__(self, enabled=False, output_path=None, ffmpeg_path='ffmpeg',
                 duration_seconds=None, pixel_format='yuv420p',
                 extra_args=None, stabilization=None, frame_rate=None,
                 split_scans=False, uniform_frame_duration=False):
        self.enabled = bool(enabled)
        self.output_path = output_path
        self.ffmpeg_path = ffmpeg_path or 'ffmpeg'
        self.duration_seconds = (float(duration_seconds)
                                 if duration_seconds is not None else None)
        self.pixel_format = pixel_format or 'yuv420p'
        self.extra_args = list(extra_args or [])
        self.stabilization = stabilization or StabilizationSettings()
        if frame_rate is None:
            rate_value = 30.0
        else:
            try:
                rate_value = float(frame_rate)
            except Exception:
                rate_value = 30.0
        if rate_value is not None and rate_value <= 0:
            rate_value = None
        self.frame_rate = rate_value
        self.split_scans = bool(split_scans)
        self.uniform_frame_duration = bool(uniform_frame_duration)


def format_time_multiplier(multiplier):
    """Return a human readable multiplier string (e.g. ``'4X'``)."""

    try:
        value = float(multiplier)
    except Exception:
        value = 0.0
    if not value or value < 0:
        value = 1.0
    # Round to two decimals then strip trailing zeros for tidy file names.
    rounded = round(value, 2)
    text = ('%.2f' % rounded).rstrip('0').rstrip('.')
    if not text:
        text = '1'
    return text + 'X'


def _detect_path_style(path):
    """Return the predominant separator style used in ``path``."""

    if not path:
        return None
    has_forward = '/' in path
    has_backward = '\\' in path
    if has_forward and not has_backward:
        return 'forward'
    if has_backward and not has_forward:
        return 'backward'
    return None


def _normalize_base_path(path):
    """Return ``path`` if it is already absolute (including Windows drives)."""

    if not path:
        return path
    if os.path.isabs(path):
        return path
    if len(path) > 1 and path[1] == ':' and path[0].isalpha():
        return path
    return os.path.abspath(path)


def _apply_path_style(path, style):
    """Normalize separators in ``path`` based on ``style``."""

    if not path or style is None:
        return path
    if style == 'forward':
        return path.replace('\\', '/')
    if style == 'backward':
        return path.replace('/', '\\')
    return path


def _join_child_path(base_path, child, style):
    """Join ``child`` to ``base_path`` while respecting ``style`` separators."""

    if not base_path:
        return child
    if style == 'forward':
        return base_path.rstrip('\\/') + '/' + child
    if style == 'backward':
        return base_path.rstrip('\\/') + '\\' + child
    return os.path.join(base_path, child)


class BatchConfig(object):
    """Container for settings used during batch processing."""

    def __init__(self, folder_path, channel_number=0, pixel_count=512,
                 file_filter=None, gwyddion_paths=None, output_directory=None,
                 video_settings=None, stabilization_settings=None,
                 channel_numbers=None, run_timestamp=None,
                 processing_options=None):
        if not folder_path:
            raise ValueError('folder_path is required')
        path_style = _detect_path_style(folder_path)
        normalized_folder = _normalize_base_path(folder_path)
        self.folder_path = _apply_path_style(normalized_folder, path_style)
        self._path_style = path_style
        self.run_timestamp = run_timestamp or datetime.datetime.now()
        if channel_numbers is None:
            channel_numbers = []
            if channel_number is not None:
                channel_numbers.append(int(channel_number))
            else:
                channel_numbers.append(0)
        normalized = []
        seen = {}
        for value in channel_numbers:
            channel = int(value)
            if channel in seen:
                continue
            seen[channel] = True
            normalized.append(channel)
        if not normalized:
            raise ValueError('At least one channel number must be provided')
        self.channel_numbers = normalized
        self.channel_number = self.channel_numbers[0]
        self.pixel_count = int(pixel_count)
        self.file_filter = (file_filter.lower() if file_filter else None)
        self.gwyddion_paths = list(gwyddion_paths or [])
        if output_directory:
            normalized_output = _normalize_base_path(output_directory)
            self.output_directory = _apply_path_style(normalized_output, path_style)
        else:
            timestamp = self.run_timestamp.strftime('output_%Y%m%d_%H%M%S')
            default_output = _join_child_path(self.folder_path, timestamp, path_style)
            self.output_directory = default_output
        if video_settings is None:
            video_settings = VideoSettings()
        if stabilization_settings is not None:
            video_settings.stabilization = stabilization_settings
        self.video = video_settings
        self.stabilization = self.video.stabilization
        if processing_options is None:
            processing_options = ProcessingOptions()
        self.processing = processing_options

    def supported_extensions(self, defaults):
        """Return the list of extensions that should be processed."""
        if self.file_filter:
            return [self.file_filter]
        return list(defaults)

    def validate(self):
        """Validate configuration values, raising ``ValueError`` on failure."""
        if not os.path.exists(self.folder_path):
            raise ValueError('Folder %r does not exist' % self.folder_path)
        if not os.path.isdir(self.folder_path):
            raise ValueError('%r is not a directory' % self.folder_path)
        if self.pixel_count <= 0:
            raise ValueError('pixel_count must be positive')
        for channel in self.channel_numbers:
            if channel < 0:
                raise ValueError('channel numbers must be non-negative')
        if (os.path.exists(self.output_directory) and
                not os.path.isdir(self.output_directory)):
            raise ValueError('%r exists and is not a directory' % self.output_directory)
        return True

    def iter_input_files(self, extensions):
        """Yield absolute file paths inside ``folder_path`` matching ``extensions``."""
        extensions = set(e.lower() for e in extensions)
        for name in sorted(os.listdir(self.folder_path)):
            path = os.path.join(self.folder_path, name)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if extensions and ext not in extensions:
                continue
            yield path

    def ensure_output_directory(self):
        """Create the output directory if it does not already exist."""
        if not os.path.isdir(self.output_directory):
            os.makedirs(self.output_directory)
        return self.output_directory

    def ensure_channel_directory(self, channel_number):
        """Return the per-channel output directory, creating it if needed."""
        name = 'channel%d' % int(channel_number)
        path = _join_child_path(self.output_directory, name, self._path_style)
        if not os.path.isdir(path):
            os.makedirs(path)
        return path

    def ensure_acf_directory(self, channel_number):
        """Return the directory for ACF images for ``channel_number``."""
        base = self.ensure_channel_directory(channel_number)
        path = _join_child_path(base, 'acf', self._path_style)
        if not os.path.isdir(path):
            os.makedirs(path)
        return path

    def get_video_output_path(self, channel_number=None, time_multiplier=None,
                              data_kind='base', scan_direction='full'):
        """Return the absolute path for the rendered video file."""
        if not self.video.enabled:
            return None
        if channel_number is None:
            if len(self.channel_numbers) == 1:
                channel_number = self.channel_numbers[0]
            else:
                raise ValueError('channel_number is required when multiple channels are configured')
        direction = scan_direction or 'full'
        data_kind = data_kind or 'base'
        suffix_parts = []
        if data_kind and data_kind != 'base':
            suffix_parts.append(data_kind)
        if direction and direction != 'full':
            suffix_parts.append(direction)
        if time_multiplier is not None:
            suffix_parts.append(format_time_multiplier(time_multiplier))
        suffix = ''
        if suffix_parts:
            suffix = '_' + '_'.join(suffix_parts)
        if self.video.output_path:
            base_path = os.path.abspath(self.video.output_path)
            base, ext = os.path.splitext(base_path)
            if not ext:
                ext = '.mp4'
            if suffix:
                base = base + suffix
            return base + ext
        channel_dir = self.ensure_channel_directory(channel_number)
        base_name = os.path.basename(os.path.normpath(self.folder_path))
        if not base_name:
            base_name = 'output'
        file_name = '%s_channel%d%s.mp4' % (base_name, channel_number, suffix)
        return os.path.join(channel_dir, file_name)
