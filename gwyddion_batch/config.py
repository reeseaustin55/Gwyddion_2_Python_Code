"""Configuration helpers for Gwyddion batch processing."""

from __future__ import absolute_import

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
                 frame_rate=None, frame_duration=0.1, pixel_format='yuv420p',
                 extra_args=None, stabilization=None):
        self.enabled = bool(enabled)
        self.output_path = output_path
        self.ffmpeg_path = ffmpeg_path or 'ffmpeg'
        self.frame_rate = float(frame_rate) if frame_rate else None
        self.frame_duration = (float(frame_duration)
                               if frame_duration is not None else None)
        self.pixel_format = pixel_format or 'yuv420p'
        self.extra_args = list(extra_args or [])
        self.stabilization = stabilization or StabilizationSettings()


class BatchConfig(object):
    """Container for settings used during batch processing."""

    def __init__(self, folder_path, channel_number=0, pixel_count=512,
                 file_filter=None, gwyddion_paths=None, output_directory=None,
                 video_settings=None, stabilization_settings=None):
        if not folder_path:
            raise ValueError('folder_path is required')
        self.folder_path = os.path.abspath(folder_path)
        self.channel_number = int(channel_number)
        self.pixel_count = int(pixel_count)
        self.file_filter = (file_filter.lower() if file_filter else None)
        self.gwyddion_paths = list(gwyddion_paths or [])
        self.output_directory = (os.path.abspath(output_directory)
                                 if output_directory
                                 else os.path.join(self.folder_path, 'processed'))
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
        if self.channel_number < 0:
            raise ValueError('channel_number must be non-negative')
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

    def get_video_output_path(self):
        """Return the absolute path for the rendered video file."""
        if not self.video.enabled:
            return None
        if self.video.output_path:
            return os.path.abspath(self.video.output_path)
        base_name = os.path.basename(os.path.normpath(self.folder_path))
        if not base_name:
            base_name = 'output'
        file_name = '%s_channel%d.mp4' % (base_name, self.channel_number)
        return os.path.join(self.output_directory, file_name)
