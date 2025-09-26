"""Configuration helpers for Gwyddion batch processing."""

from __future__ import absolute_import

import datetime
import os


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
                 extra_args=None, stabilization=None):
        self.enabled = bool(enabled)
        self.output_path = output_path
        self.ffmpeg_path = ffmpeg_path or 'ffmpeg'
        self.duration_seconds = (float(duration_seconds)
                                 if duration_seconds is not None else None)
        self.pixel_format = pixel_format or 'yuv420p'
        self.extra_args = list(extra_args or [])
        self.stabilization = stabilization or StabilizationSettings()


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


class BatchConfig(object):
    """Container for settings used during batch processing."""

    def __init__(self, folder_path, channel_number=0, pixel_count=512,
                 file_filter=None, gwyddion_paths=None, output_directory=None,
                 video_settings=None, stabilization_settings=None,
                 channel_numbers=None, run_timestamp=None):
        if not folder_path:
            raise ValueError('folder_path is required')
        self.folder_path = os.path.abspath(folder_path)
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
            self.output_directory = os.path.abspath(output_directory)
        else:
            timestamp = self.run_timestamp.strftime('outputs_%Y%m%d_%H%M%S')
            self.output_directory = os.path.join(self.folder_path, timestamp)
        if video_settings is None:
            video_settings = VideoSettings()
        if stabilization_settings is not None:
            video_settings.stabilization = stabilization_settings
        self.video = video_settings
        self.stabilization = self.video.stabilization

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

    def get_video_output_path(self, channel_number=None, time_multiplier=None):
        """Return the absolute path for the rendered video file."""
        if not self.video.enabled:
            return None
        if channel_number is None:
            if len(self.channel_numbers) == 1:
                channel_number = self.channel_numbers[0]
            else:
                raise ValueError('channel_number is required when multiple channels are configured')
        if self.video.output_path:
            base_path = os.path.abspath(self.video.output_path)
            base, ext = os.path.splitext(base_path)
            if not ext:
                ext = '.mp4'
            if time_multiplier is not None:
                base = '%s_%s' % (base, format_time_multiplier(time_multiplier))
            return base + ext
        base_name = os.path.basename(os.path.normpath(self.folder_path))
        if not base_name:
            base_name = 'output'
        multiplier_part = ''
        if time_multiplier is not None:
            multiplier_part = '_%s' % format_time_multiplier(time_multiplier)
        file_name = '%s_channel%d%s.mp4' % (base_name, channel_number, multiplier_part)
        return os.path.join(self.output_directory, file_name)
