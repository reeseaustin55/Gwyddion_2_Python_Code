"""Helpers for stitching processed images into a video."""

from __future__ import absolute_import

import io
import math
import os
import re
import struct
import subprocess
import sys
import tempfile

from .compat import text_type, to_native_path

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
FLOAT_PATTERN = re.compile(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?')


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


def _contains_filter(extra_args):
    """Return ``True`` if ffmpeg filter arguments are present."""
    for index, arg in enumerate(extra_args):
        value = arg
        if not isinstance(value, text_type):
            try:
                value = text_type(value)
            except Exception:
                value = text_type(str(value))
        flag = value.lower()
        if flag in ('-vf', '-filter:v', '-filter_complex', '-filter_complex_script'):
            return True
        if flag.startswith('-filter'):  # catch variations
            return True
    return False


def _probe_png_size(path):
    """Return ``(width, height)`` for a PNG ``path``."""
    handle = open(path, 'rb')
    try:
        signature = handle.read(8)
        if signature != PNG_SIGNATURE:
            raise ValueError('Only PNG images are supported for stabilization cropping: %s' % path)
        length_bytes = handle.read(4)
        if len(length_bytes) != 4:
            raise ValueError('Failed to read PNG chunk length from %s' % path)
        length = struct.unpack('>I', length_bytes)[0]
        chunk_type = handle.read(4)
        if chunk_type != b'IHDR':
            raise ValueError('Unexpected PNG chunk %r in %s' % (chunk_type, path))
        data = handle.read(length)
        if len(data) < 8:
            raise ValueError('Incomplete IHDR chunk in %s' % path)
        width, height = struct.unpack('>II', data[:8])
        return width, height
    finally:
        handle.close()


def _parse_transforms(path):
    """Parse vidstab transform file and return lists of dx, dy values."""
    dxs = []
    dys = []
    dx_pattern = re.compile(r'dx\s*=\s*(%s)' % FLOAT_PATTERN.pattern)
    dy_pattern = re.compile(r'dy\s*=\s*(%s)' % FLOAT_PATTERN.pattern)
    handle = io.open(path, 'r', encoding='utf-8')
    try:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            dx_match = dx_pattern.search(stripped)
            dy_match = dy_pattern.search(stripped)
            if dx_match and dy_match:
                dxs.append(float(dx_match.group(1)))
                dys.append(float(dy_match.group(1)))
                continue
            parts = stripped.split(':', 1)
            if len(parts) == 2:
                payload = parts[1]
            else:
                payload = stripped
            numbers = FLOAT_PATTERN.findall(payload)
            if len(numbers) >= 3:
                dxs.append(float(numbers[0]))
                dys.append(float(numbers[1]))
            elif len(numbers) >= 2:
                dxs.append(float(numbers[-2]))
                dys.append(float(numbers[-1]))
    finally:
        handle.close()
    return dxs, dys


def _compute_crop_region(dxs, dys, width, height):
    """Compute crop rectangle ensuring overlap between frames."""
    if not dxs or not dys:
        return None
    min_right = min(dx + float(width) for dx in dxs)
    min_bottom = min(dy + float(height) for dy in dys)
    max_left = max(max(dx, 0.0) for dx in dxs)
    max_top = max(max(dy, 0.0) for dy in dys)
    crop_width = int(math.floor(min_right) - int(math.ceil(max_left)))
    crop_height = int(math.floor(min_bottom) - int(math.ceil(max_top)))
    if crop_width <= 0 or crop_height <= 0:
        return None
    crop_x = int(math.ceil(max_left))
    crop_y = int(math.ceil(max_top))
    if crop_x < 0:
        crop_x = 0
    if crop_y < 0:
        crop_y = 0
    if crop_x + crop_width > width:
        crop_width = width - crop_x
    if crop_y + crop_height > height:
        crop_height = height - crop_y
    if crop_width <= 0 or crop_height <= 0:
        return None
    if crop_width % 2:
        crop_width -= 1
    if crop_height % 2:
        crop_height -= 1
    if crop_width <= 0 or crop_height <= 0:
        return None
    return crop_x, crop_y, crop_width, crop_height


def _build_detect_filter(stabilization, transform_path):
    parts = [
        'vidstabdetect',
        'result=%s' % _escape_path(_ensure_text(transform_path)),
        'shakiness=%d' % stabilization.shakiness,
        'accuracy=%d' % stabilization.accuracy,
        'stepsize=%d' % stabilization.stepsize,
        'mincontrast=%.6f' % stabilization.mincontrast,
    ]
    if stabilization.tripod:
        parts.append('tripod=1')
    return ':'.join(parts)


def _build_transform_filter(stabilization, transform_path):
    parts = [
        'vidstabtransform',
        'input=%s' % _escape_path(_ensure_text(transform_path)),
        'smoothing=%d' % stabilization.smoothing,
        'optzoom=0',
        'zoom=0',
        'interpol=bicubic',
    ]
    if stabilization.tripod:
        parts.append('tripod=1')
    return ':'.join(parts)


def stitch_images_to_video(image_paths, output_path, ffmpeg_path='ffmpeg',
                           frame_rate=None, frame_duration=None,
                           pixel_format='yuv420p', extra_args=None,
                           logger=None, stabilization=None):
    """Combine ``image_paths`` into a video using ffmpeg."""
    if not image_paths:
        raise ValueError('image_paths must not be empty')

    extra_args = list(extra_args or [])

    directory = os.path.dirname(output_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)

    list_path = None
    transform_path = None
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

        filters = []
        if stabilization and getattr(stabilization, 'enabled', False):
            if _contains_filter(extra_args):
                raise ValueError('Custom ffmpeg filter arguments are not compatible with stabilization')
            transform_handle, transform_path = tempfile.mkstemp(prefix='gwyddion_transforms_', suffix='.trf')
            os.close(transform_handle)

            detect_filter = _build_detect_filter(stabilization, transform_path)
            detect_cmd = [
                to_native_path(ffmpeg_path),
                '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', to_native_path(list_path),
                '-vf', detect_filter,
                '-f', 'null',
                '-',
            ]
            if logger:
                logger.info('Analyzing frame drift for stabilization')
            subprocess.check_call(detect_cmd)

            if stabilization.crop_shared_area:
                try:
                    width, height = _probe_png_size(image_paths[0])
                    dxs, dys = _parse_transforms(transform_path)
                    crop_rect = _compute_crop_region(dxs, dys, width, height)
                except Exception as exc:
                    crop_rect = None
                    if logger:
                        logger.warning('Unable to compute crop region: %s', exc)
            else:
                crop_rect = None

            filters.append(_build_transform_filter(stabilization, transform_path))
            if crop_rect:
                crop_x, crop_y, crop_w, crop_h = crop_rect
                if logger:
                    logger.info('Cropping stabilized video to %dx%d at %d,%d', crop_w, crop_h, crop_x, crop_y)
                filters.append('crop=%d:%d:%d:%d' % (crop_w, crop_h, crop_x, crop_y))

        if filters:
            command.extend(['-vf', ','.join(filters)])

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
        if transform_path and os.path.exists(transform_path):
            try:
                os.remove(transform_path)
            except OSError:
                pass
