"""Helpers for stitching processed images into a video."""

from __future__ import absolute_import

import io
import os
import subprocess
import sys
import tempfile

from .compat import text_type, to_native_path


def _ensure_text(value):
    """Return ``value`` as a unicode string."""
    if isinstance(value, text_type):
        return value
    encoding = sys.getfilesystemencoding() or 'utf-8'
    try:
        return value.decode(encoding)
    except Exception:
        return value.decode(encoding, 'replace')


def _escape_path(path):
    """Escape single quotes for ffmpeg concat files."""
    return path.replace(u"'", u"'\\''")


def stitch_images_to_video(image_paths, output_path, ffmpeg_path='ffmpeg',
                           frame_rate=None, frame_duration=None,
                           pixel_format='yuv420p', extra_args=None,
                           logger=None):
    """Combine ``image_paths`` into a video using ffmpeg."""
    if not image_paths:
        raise ValueError('image_paths must not be empty')

    extra_args = list(extra_args or [])

    directory = os.path.dirname(output_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)

    list_path = None
    try:
        list_handle, list_path = tempfile.mkstemp(prefix='gwyddion_frames_', suffix='.txt')
        os.close(list_handle)
        with io.open(list_path, 'w', encoding='utf-8') as handle:
            for path in image_paths:
                text_path = _ensure_text(path)
                handle.write(u"file '%s'\n" % _escape_path(text_path))
                if frame_duration is not None:
                    handle.write(u'duration %.6f\n' % float(frame_duration))

        command = [
            to_native_path(ffmpeg_path),
            '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', to_native_path(list_path),
        ]

        if frame_rate:
            command.extend(['-r', str(frame_rate)])
        if frame_duration is not None:
            command.extend(['-vsync', 'vfr'])
        if pixel_format:
            command.extend(['-pix_fmt', pixel_format])
        for arg in extra_args:
            if isinstance(arg, text_type):
                command.append(to_native_path(arg))
            else:
                command.append(to_native_path(text_type(arg)))
        command.append(to_native_path(output_path))

        if logger:
            logger.info('Running ffmpeg to create %s', output_path)
            try:
                printable_cmd = u' '.join(_ensure_text(part) for part in command)
            except Exception:
                printable_cmd = command
            logger.debug('ffmpeg command: %s', printable_cmd)

        subprocess.check_call(command)
        return output_path
    finally:
        if list_path and os.path.exists(list_path):
            try:
                os.remove(list_path)
            except OSError:
                pass
