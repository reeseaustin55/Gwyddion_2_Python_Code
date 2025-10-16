"""Helpers for stitching processed images into a video."""

from __future__ import absolute_import

import io
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

from .compat import text_type, to_native_path

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


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


def _calculate_max_shift_pixels(stabilization, frame_width):
    """Return the maximum drift in pixels based on ``stabilization`` settings."""
    if not stabilization or frame_width is None:
        return None
    try:
        percent = float(getattr(stabilization, 'max_displacement_percent', 0.0))
    except Exception:
        percent = 0.0
    if percent <= 0:
        return None
    return max(0.0, (percent / 100.0) * float(frame_width))


def _determine_detection_geometry(width, height):
    if width is None or height is None or width <= 0 or height <= 0:
        return None, None, None
    max_width = 256
    target_width = min(max_width, int(width))
    if target_width <= 0:
        target_width = int(width)
    if target_width <= 0:
        target_width = 1
    scale_ratio = float(target_width) / float(width)
    target_height = int(round(float(height) * scale_ratio))
    if target_height <= 0:
        target_height = 1
    if target_width == width and target_height == height:
        scale_filter = None
    else:
        scale_filter = 'scale=%d:%d' % (target_width, target_height)
    return target_width, target_height, scale_filter


def _load_frame_bytes(path, ffmpeg_path, width, height, scale_filter=None):
    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError('Invalid frame dimensions for %s' % path)
    command = [
        to_native_path(ffmpeg_path),
        '-v', 'error',
        '-i', to_native_path(path),
    ]
    if scale_filter:
        command.extend(['-vf', scale_filter])
    command.extend(['-f', 'rawvideo', '-pix_fmt', 'gray', '-'])
    data = subprocess.check_output(command)
    expected = int(width) * int(height)
    if len(data) < expected:
        raise ValueError('Incomplete raw frame from ffmpeg for %s' % path)
    if len(data) > expected:
        data = data[:expected]
    return bytearray(data)


def _sum_abs_diff(reference, frame, width, height, dx, dy, best_score=None):
    x_start = max(0, dx)
    x_end = min(width, width + dx)
    y_start = max(0, dy)
    y_end = min(height, height + dy)
    if x_end <= x_start or y_end <= y_start:
        return None
    stride = width
    total = 0
    for y in range(y_start, y_end):
        ref_index = y * stride + x_start
        frame_index = (y - dy) * stride + (x_start - dx)
        row_length = x_end - x_start
        for offset in range(row_length):
            total += abs(reference[ref_index + offset] - frame[frame_index + offset])
            if best_score is not None and total >= best_score:
                return total
    return total


def _find_best_translation(reference, frame, width, height, search_radius,
                            center_dx=0, center_dy=0, max_allowed=None):
    if search_radius is None or search_radius < 0:
        search_radius = 0
    if max_allowed is not None:
        try:
            max_allowed = int(math.floor(max_allowed))
        except Exception:
            max_allowed = None
        if max_allowed is not None and max_allowed < 0:
            max_allowed = 0
    center_dx = int(round(center_dx))
    center_dy = int(round(center_dy))
    radius = int(math.ceil(search_radius))
    best_score = None
    best_dx = 0
    best_dy = 0
    dx_min = center_dx - radius
    dx_max = center_dx + radius
    dy_min = center_dy - radius
    dy_max = center_dy + radius
    if max_allowed is not None:
        dx_min = max(dx_min, -max_allowed)
        dx_max = min(dx_max, max_allowed)
        dy_min = max(dy_min, -max_allowed)
        dy_max = min(dy_max, max_allowed)
    for dy in range(dy_min, dy_max + 1):
        for dx in range(dx_min, dx_max + 1):
            diff = _sum_abs_diff(reference, frame, width, height, dx, dy, best_score)
            if diff is None:
                continue
            if best_score is None or diff < best_score:
                best_score = diff
                best_dx = dx
                best_dy = dy
    return best_dx, best_dy


def _estimate_frame_translations(image_paths, ffmpeg_path, width, height,
                                 max_shift_px, logger=None):
    if not image_paths:
        return []
    detection_width, detection_height, scale_filter = _determine_detection_geometry(width, height)
    if detection_width is None or detection_height is None:
        return []

    try:
        prev_small = _load_frame_bytes(image_paths[0], ffmpeg_path,
                                       detection_width, detection_height,
                                       scale_filter)
        prev_full = _load_frame_bytes(image_paths[0], ffmpeg_path,
                                      width, height, None)
    except Exception as exc:
        if logger:
            logger.warning('Unable to load frame %s for stabilization: %s',
                           image_paths[0], exc)
        return []

    translations = [(0, 0)]
    cumulative_dx = 0
    cumulative_dy = 0

    if max_shift_px is not None:
        coarse_limit = int(max(0, round(max_shift_px * float(detection_width) / float(width))))
        fine_limit = int(max(0, round(max_shift_px)))
    else:
        coarse_limit = max(4, detection_width // 8)
        fine_limit = None

    fine_radius = 3
    for path in image_paths[1:]:
        try:
            curr_small = _load_frame_bytes(path, ffmpeg_path,
                                           detection_width, detection_height,
                                           scale_filter)
            curr_full = _load_frame_bytes(path, ffmpeg_path,
                                          width, height, None)
        except Exception as exc:
            if logger:
                logger.warning('Unable to load frame %s for stabilization: %s',
                               path, exc)
            return []

        dx_small, dy_small = _find_best_translation(
            prev_small, curr_small, detection_width, detection_height,
            coarse_limit, center_dx=0, center_dy=0,
            max_allowed=coarse_limit if max_shift_px is not None else None,
        )
        approx_dx = dx_small * (float(width) / float(detection_width))
        approx_dy = dy_small * (float(height) / float(detection_height))

        dx_full, dy_full = _find_best_translation(
            prev_full, curr_full, width, height,
            search_radius=fine_radius,
            center_dx=approx_dx, center_dy=approx_dy,
            max_allowed=fine_limit,
        )

        cumulative_dx += dx_full
        cumulative_dy += dy_full
        translations.append((cumulative_dx, cumulative_dy))

        prev_small = curr_small
        prev_full = curr_full

    return translations


def _compute_shared_crop(translations, width, height):
    if not translations:
        return None
    dxs = [offset[0] for offset in translations]
    dys = [offset[1] for offset in translations]
    x_min = int(math.ceil(max(dxs)))
    x_max = int(math.floor(min(width + dx for dx in dxs)))
    y_min = int(math.ceil(max(dys)))
    y_max = int(math.floor(min(height + dy for dy in dys)))
    crop_w = x_max - x_min
    crop_h = y_max - y_min
    if crop_w <= 1 or crop_h <= 1:
        return None
    return x_min, y_min, crop_w, crop_h


def _render_stabilized_frames(image_paths, ffmpeg_path, translations,
                              crop_info, width, height):
    x_min, y_min, crop_w, crop_h = crop_info
    temp_dir = tempfile.mkdtemp(prefix='gwyddion_stabilized_')
    stabilized_paths = []
    for index, path in enumerate(image_paths):
        dx, dy = translations[index]
        crop_x = x_min - dx
        crop_y = y_min - dy
        crop_x = int(max(0, min(width - crop_w, crop_x)))
        crop_y = int(max(0, min(height - crop_h, crop_y)))
        output_path = os.path.join(temp_dir, 'frame_%06d.png' % index)
        command = [
            to_native_path(ffmpeg_path),
            '-v', 'error',
            '-y',
            '-i', to_native_path(path),
            '-vf', 'crop=%d:%d:%d:%d' % (crop_w, crop_h, crop_x, crop_y),
            '-frames:v', '1',
            to_native_path(output_path),
        ]
        subprocess.check_call(command)
        stabilized_paths.append(output_path)
    return temp_dir, stabilized_paths


def _prepare_stabilized_sequence(image_paths, width, height, ffmpeg_path,
                                 stabilization, logger=None):
    if width is None or height is None:
        return None
    max_shift_px = _calculate_max_shift_pixels(stabilization, width)
    translations = _estimate_frame_translations(
        image_paths,
        ffmpeg_path,
        int(width),
        int(height),
        max_shift_px,
        logger=logger,
    )
    if not translations:
        return None
    crop_info = _compute_shared_crop(translations, int(width), int(height))
    if crop_info is None:
        return None
    temp_dir, stabilized_paths = _render_stabilized_frames(
        image_paths,
        ffmpeg_path,
        translations,
        crop_info,
        int(width),
        int(height),
    )
    return {
        'paths': stabilized_paths,
        'temp_dir': temp_dir,
        'width': crop_info[2],
        'height': crop_info[3],
        'x_offset': crop_info[0],
        'y_offset': crop_info[1],
    }


def _calculate_repeat_counts(durations, frame_count, default_duration, frame_rate):
    """Return repeat counts per frame to approximate ``durations`` at ``frame_rate``."""
    if frame_count <= 0:
        return []

    if default_duration is None or default_duration <= 0:
        default_duration = 1.0 / float(frame_rate)

    normalized = []
    for index in range(frame_count):
        value = default_duration
        if index < len(durations):
            candidate = durations[index]
            try:
                candidate = float(candidate)
            except Exception:
                candidate = None
            if candidate and candidate > 0:
                value = candidate
        normalized.append(value)

    total_duration = sum(normalized)
    if total_duration <= 0:
        total_duration = default_duration * frame_count

    target_total_frames = int(round(total_duration * float(frame_rate)))
    if target_total_frames < frame_count:
        target_total_frames = frame_count

    repeats = []
    produced = 0
    accumulator = 0.0
    for value in normalized:
        accumulator += value * float(frame_rate)
        count = int(round(accumulator)) - produced
        if count <= 0:
            count = 1
        repeats.append(count)
        produced += count

    if repeats:
        diff = target_total_frames - produced
        if diff != 0:
            repeats[-1] = max(1, repeats[-1] + diff)

    return repeats


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


def stitch_images_to_video(image_paths, output_path, ffmpeg_path='ffmpeg',
                           frame_durations=None, frame_duration=None,
                           pixel_format='yuv420p', extra_args=None,
                           frame_rate=None,
                           logger=None, stabilization=None):
    """Combine ``image_paths`` into a video using ffmpeg."""
    if not image_paths:
        raise ValueError('image_paths must not be empty')

    extra_args = list(extra_args or [])

    directory = os.path.dirname(output_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)

    list_path = None
    ensure_even_filter = None
    width = None
    height = None
    stabilized_temp_dir = None

    try:
        first_path = image_paths[0]
        width, height = _probe_png_size(first_path)
    except Exception as exc:
        if logger:
            logger.debug('Unable to read image dimensions for stabilization planning: %s', exc)
        width = None
        height = None

    if stabilization and getattr(stabilization, 'enabled', False):
        if _contains_filter(extra_args):
            raise ValueError('Custom ffmpeg filter arguments are not compatible with stabilization')
        stabilization_result = None
        stabilization_failed = False
        try:
            stabilization_result = _prepare_stabilized_sequence(
                image_paths,
                width,
                height,
                ffmpeg_path,
                stabilization,
                logger=logger,
            )
        except Exception as exc:
            if logger:
                logger.warning('Stabilization failed (%s); continuing without stabilization.', exc)
            stabilization_failed = True
        if stabilization_result:
            if logger:
                logger.info(
                    'Applying translation-based stabilization; cropped frame size %dx%d',
                    stabilization_result['width'],
                    stabilization_result['height'],
                )
            stabilized_temp_dir = stabilization_result['temp_dir']
            image_paths = stabilization_result['paths']
            width = stabilization_result.get('width', width)
            height = stabilization_result.get('height', height)
        elif logger and not stabilization_failed:
            logger.warning('Stabilization did not produce a usable crop; continuing without stabilization.')

    if (width is None or height is None) and image_paths:
        try:
            width, height = _probe_png_size(image_paths[0])
        except Exception as exc:
            if logger:
                logger.debug('Unable to read image dimensions for even scaling: %s', exc)
            width = None
            height = None

    if width is not None and height is not None and (width % 2 or height % 2):
        ensure_even_filter = 'scale=ceil(iw/2)*2:ceil(ih/2)*2'
        if logger:
            logger.info('Ensuring even frame dimensions for video output (%dx%d)', width, height)

    try:
        list_handle, list_path = tempfile.mkstemp(prefix='gwyddion_frames_', suffix='.txt')
        os.close(list_handle)
        try:
            sanitized_rate = float(frame_rate) if frame_rate is not None else None
        except Exception:
            sanitized_rate = None
        if sanitized_rate is not None and sanitized_rate <= 0:
            sanitized_rate = None

        durations = list(frame_durations or [])
        default_duration = None
        if frame_duration is not None:
            try:
                default_duration = float(frame_duration)
            except Exception:
                default_duration = None
        using_repeats = sanitized_rate is not None
        repeat_counts = None
        if using_repeats:
            repeat_counts = _calculate_repeat_counts(
                durations,
                len(image_paths),
                default_duration,
                float(sanitized_rate),
            )
        with io.open(list_path, 'w', encoding='utf-8') as handle:
            if using_repeats and repeat_counts:
                frame_interval = 1.0 / float(sanitized_rate)
                last_escaped = None
                for path, count in zip(image_paths, repeat_counts):
                    text_path = _ensure_text(path)
                    escaped = _escape_path(text_path)
                    for _ in range(count):
                        handle.write(u"file '%s'\n" % escaped)
                        handle.write(u'duration %.9f\n' % frame_interval)
                        last_escaped = escaped
                if last_escaped is not None:
                    handle.write(u"file '%s'\n" % last_escaped)
            else:
                if default_duration is None and sanitized_rate:
                    default_duration = 1.0 / float(sanitized_rate)
                wrote_duration = False
                last_escaped = None
                for index, path in enumerate(image_paths):
                    text_path = _ensure_text(path)
                    escaped = _escape_path(text_path)
                    handle.write(u"file '%s'\n" % escaped)
                    last_escaped = escaped
                    duration_value = None
                    if index < len(durations):
                        duration_value = durations[index]
                    elif default_duration is not None:
                        duration_value = default_duration
                    if duration_value is None and sanitized_rate:
                        duration_value = 1.0 / float(sanitized_rate)
                    if duration_value is None:
                        continue
                    try:
                        duration_float = float(duration_value)
                    except Exception:
                        duration_float = 0.0
                    if duration_float <= 0:
                        duration_float = 1e-3
                    handle.write(u'duration %.9f\n' % duration_float)
                    wrote_duration = True
                if wrote_duration and last_escaped is not None:
                    handle.write(u"file '%s'\n" % last_escaped)

        command = [
            to_native_path(ffmpeg_path),
            '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', to_native_path(list_path),
        ]

        filters = []
        if ensure_even_filter:
            filters.append(ensure_even_filter)

        if filters:
            command.extend(['-vf', ','.join(filters)])

        if sanitized_rate:
            command.extend(['-fps_mode', 'cfr'])
            command.extend(['-r', '%.6f' % float(sanitized_rate)])
        elif frame_durations or frame_duration is not None:
            command.extend(['-fps_mode', 'vfr'])
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
        if stabilized_temp_dir and os.path.isdir(stabilized_temp_dir):
            try:
                shutil.rmtree(stabilized_temp_dir)
            except OSError:
                pass
