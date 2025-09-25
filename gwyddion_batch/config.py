"""Configuration helpers for Gwyddion batch processing."""

from __future__ import absolute_import

import os


class BatchConfig(object):
    """Container for settings used during batch processing."""

    def __init__(self, folder_path, channel_number=0, pixel_count=512,
                 file_filter=None, gwyddion_paths=None):
        if not folder_path:
            raise ValueError('folder_path is required')
        self.folder_path = os.path.abspath(folder_path)
        self.channel_number = int(channel_number)
        self.pixel_count = int(pixel_count)
        self.file_filter = (file_filter.lower() if file_filter else None)
        self.gwyddion_paths = list(gwyddion_paths or [])

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
