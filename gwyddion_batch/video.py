"""Helpers for stitching processed images into a video."""

from __future__ import absolute_import

import binascii
import io
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

import numpy as np

from .compat import text_type, to_native_path

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _write_png_chunk(handle, chunk_type, data):
    length = len(data)
    handle.write(struct.pack('>I', length))
    handle.write(chunk_type)
    if data:
        handle.write(data)
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(data, crc)
    handle.write(struct.pack('>I', crc & 0xFFFFFFFF))


def _write_png(path, array):
    array = np.asarray(array)
    if array.ndim != 2:
        raise ValueError('Only 2D grayscale arrays can be written as PNG images')
    if array.dtype != np.uint8:
        array = np.clip(np.rint(array), 0, 255).astype(np.uint8)
    height, width = array.shape
    rows = []
    for row in np.ascontiguousarray(array):
        rows.append(b'\x00' + row.tobytes())
    compressed = zlib.compress(b''.join(rows), 9)
    with open(path, 'wb') as handle:
        handle.write(PNG_SIGNATURE)
        ihdr = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)
        _write_png_chunk(handle, b'IHDR', ihdr)
        _write_png_chunk(handle, b'IDAT', compressed)
        _write_png_chunk(handle, b'IEND', b'')


def _paeth_predictor(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _apply_png_filter(filter_type, data, prev, bpp):
    if filter_type == 0:
        return data
    result = bytearray(len(data))
    if filter_type == 1:
        for i, value in enumerate(data):
            left = result[i - bpp] if i >= bpp else 0
            result[i] = (value + left) & 0xFF
        return result
    if filter_type == 2:
        for i, value in enumerate(data):
            up = prev[i] if prev is not None else 0
            result[i] = (value + up) & 0xFF
        return result
    if filter_type == 3:
        for i, value in enumerate(data):
            left = result[i - bpp] if i >= bpp else 0
            up = prev[i] if prev is not None else 0
            result[i] = (value + ((left + up) // 2)) & 0xFF
        return result
    if filter_type == 4:
        for i, value in enumerate(data):
            left = result[i - bpp] if i >= bpp else 0
            up = prev[i] if prev is not None else 0
            up_left = prev[i - bpp] if prev is not None and i >= bpp else 0
            result[i] = (value + _paeth_predictor(left, up, up_left)) & 0xFF
        return result
    raise ValueError('Unsupported PNG filter %d' % filter_type)


def _read_png_gray(path):
    handle = open(path, 'rb')
    try:
        signature = handle.read(8)
        if signature != PNG_SIGNATURE:
            raise ValueError('Only grayscale PNG images are supported: %s' % path)
        width = height = None
        bit_depth = None
        color_type = None
        interlace = 0
        idat_chunks = []
        while True:
            length_bytes = handle.read(4)
            if len(length_bytes) != 4:
                break
            length = struct.unpack('>I', length_bytes)[0]
            chunk_type = handle.read(4)
            data = handle.read(length)
            handle.read(4)  # CRC
            if chunk_type == b'IHDR':
                width, height, bit_depth, color_type, _, _, interlace = struct.unpack('>IIBBBBB', data)
            elif chunk_type == b'IDAT':
                idat_chunks.append(data)
            elif chunk_type == b'IEND':
                break
        if width is None or height is None:
            raise ValueError('PNG %s is missing IHDR data' % path)
        if interlace != 0:
            raise ValueError('Interlaced PNG images are not supported: %s' % path)
        if color_type not in (0, 4):
            raise ValueError('Unsupported PNG color type %d in %s' % (color_type, path))
        if bit_depth not in (8, 16):
            raise ValueError('Unsupported PNG bit depth %d in %s' % (bit_depth, path))
        channels = 1 if color_type == 0 else 2
        bpp = max(1, (bit_depth * channels + 7) // 8)
        decompressed = zlib.decompress(b''.join(idat_chunks))
        row_bytes = ((bit_depth * channels * width) + 7) // 8
        expected = height * (row_bytes + 1)
        if len(decompressed) < expected:
            raise ValueError('Incomplete pixel data in %s' % path)
        rows = []
        offset = 0
        prev_row = None
        for _ in range(height):
            filter_type = decompressed[offset]
            offset += 1
            raw = bytearray(decompressed[offset:offset + row_bytes])
            offset += row_bytes
            recon = _apply_png_filter(filter_type, raw, prev_row, bpp)
            rows.append(bytes(recon))
            prev_row = recon
        if bit_depth == 16:
            dtype = '>u2'
            row_arrays = [np.frombuffer(row, dtype=dtype).reshape(width, channels) for row in rows]
            stacked = np.stack(row_arrays, axis=0)
            grayscale = stacked[..., 0]
            grayscale = (grayscale / 257.0).astype(np.uint8)
        else:
            row_arrays = [np.frombuffer(row, dtype=np.uint8).reshape(width, channels) for row in rows]
            stacked = np.stack(row_arrays, axis=0)
            grayscale = stacked[..., 0]
        return grayscale.astype(np.uint8)
    finally:
        handle.close()


def _resize_grayscale_linear(image, target_width, target_height):
    source = np.asarray(image, dtype=np.float32)
    height, width = source.shape
    if target_width == width and target_height == height:
        return source
    if target_width <= 0 or target_height <= 0:
        raise ValueError('Invalid target size %sx%s' % (target_width, target_height))
    x_positions = np.linspace(0, max(0, width - 1), int(target_width))
    temp = np.empty((height, int(target_width)), dtype=np.float32)
    base_x = np.arange(width, dtype=np.float32)
    for row in range(height):
        temp[row, :] = np.interp(x_positions, base_x, source[row, :])
    y_positions = np.linspace(0, max(0, height - 1), int(target_height))
    resized = np.empty((int(target_height), int(target_width)), dtype=np.float32)
    base_y = np.arange(height, dtype=np.float32)
    for col in range(int(target_width)):
        resized[:, col] = np.interp(y_positions, base_y, temp[:, col])
    return resized


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
        return None, None
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
    return target_width, target_height


def _load_png_frame(path):
    frame = _read_png_gray(path)
    if frame.ndim != 2:
        raise ValueError('Only 2D grayscale images are supported for stabilization: %s' % path)
    return frame


def _sum_abs_diff(reference, frame, width, height, dx, dy, best_score=None):
    dx = int(dx)
    dy = int(dy)
    width = int(width)
    height = int(height)
    x_start = max(0, dx)
    x_end = min(width, width + dx)
    y_start = max(0, dy)
    y_end = min(height, height + dy)
    if x_end <= x_start or y_end <= y_start:
        return None
    ref_slice = reference[y_start:y_end, x_start:x_end]
    frame_slice = frame[(y_start - dy):(y_end - dy), (x_start - dx):(x_end - dx)]
    if ref_slice.size == 0 or frame_slice.size == 0:
        return None
    diff = np.abs(ref_slice.astype(np.int32) - frame_slice.astype(np.int32))
    total = float(diff.sum())
    if best_score is not None and total >= best_score:
        return total
    return total


def _phase_correlation_translation(reference, frame, max_allowed=None):
    if reference is None or frame is None:
        return 0, 0
    ref_array = np.asarray(reference, dtype=np.float32)
    frame_array = np.asarray(frame, dtype=np.float32)
    if ref_array.size == 0 or frame_array.size == 0:
        return 0, 0
    ref_array = ref_array - ref_array.mean()
    frame_array = frame_array - frame_array.mean()
    fft_ref = np.fft.fft2(ref_array)
    fft_frame = np.fft.fft2(frame_array)
    cross_power = fft_ref * np.conj(fft_frame)
    magnitude = np.abs(cross_power)
    magnitude[magnitude == 0] = 1.0
    cross_power /= magnitude
    correlation = np.fft.ifft2(cross_power)
    correlation = np.abs(correlation)
    max_pos = np.unravel_index(np.argmax(correlation), correlation.shape)
    dy, dx = max_pos
    height, width = reference.shape
    if dx > width // 2:
        dx -= width
    if dy > height // 2:
        dy -= height
    if max_allowed is not None:
        try:
            limit = int(max_allowed)
            dx = int(max(-limit, min(limit, dx)))
            dy = int(max(-limit, min(limit, dy)))
        except Exception:
            pass
    return int(dx), int(dy)


def _refine_translation(reference, frame, approx_dx, approx_dy, max_allowed=None,
                        window_radius=3):
    if reference is None or frame is None:
        return int(round(approx_dx)), int(round(approx_dy))
    height, width = reference.shape
    center_dx = int(round(approx_dx))
    center_dy = int(round(approx_dy))
    if window_radius is None or window_radius < 1:
        window_radius = 1
    limit = None
    if max_allowed is not None:
        try:
            limit = int(max_allowed)
        except Exception:
            limit = None
    best_dx = center_dx
    best_dy = center_dy
    best_score = None
    visited = set()
    current_radius = window_radius
    anchor_dx = center_dx
    anchor_dy = center_dy
    if limit is not None:
        max_radius = max(1, limit)
    else:
        max_radius = max(1, min(max(width, height) // 2, 512))

    while True:
        improved = False
        start_y = anchor_dy - current_radius
        end_y = anchor_dy + current_radius
        start_x = anchor_dx - current_radius
        end_x = anchor_dx + current_radius
        for dy in range(start_y, end_y + 1):
            if limit is not None and abs(dy) > limit:
                continue
            for dx in range(start_x, end_x + 1):
                if limit is not None and abs(dx) > limit:
                    continue
                key = (dx, dy)
                if key in visited:
                    continue
                visited.add(key)
                diff = _sum_abs_diff(reference, frame, width, height, dx, dy, best_score)
                if diff is None:
                    continue
                if best_score is None or diff < best_score:
                    best_score = diff
                    best_dx = dx
                    best_dy = dy
                    improved = True
        if not improved:
            break
        if limit is not None and (abs(best_dx) >= limit or abs(best_dy) >= limit):
            break
        boundary_hit = (
            abs(best_dx - anchor_dx) >= current_radius or
            abs(best_dy - anchor_dy) >= current_radius
        )
        if not boundary_hit:
            break
        if current_radius >= max_radius:
            break
        new_radius = min(current_radius * 2, max_radius)
        if new_radius == current_radius:
            break
        anchor_dx = best_dx
        anchor_dy = best_dy
        current_radius = new_radius

    if limit is not None:
        best_dx = max(-limit, min(limit, best_dx))
        best_dy = max(-limit, min(limit, best_dy))
    return int(best_dx), int(best_dy)


def _estimate_frame_translations(image_paths, ffmpeg_path, width, height,
                                 max_shift_px, logger=None):
    if not image_paths:
        return [], []
    try:
        prev_full = _load_png_frame(image_paths[0])
    except Exception as exc:
        if logger:
            logger.warning('Unable to load frame %s for stabilization: %s',
                           image_paths[0], exc)
        return [], []

    full_height, full_width = prev_full.shape
    if width is None:
        width = full_width
    if height is None:
        height = full_height
    detection_width, detection_height = _determine_detection_geometry(width, height)
    if detection_width is None or detection_height is None:
        return [], []

    if detection_width == full_width and detection_height == full_height:
        prev_small = prev_full.astype(np.float32)
    else:
        prev_small = _resize_grayscale_linear(prev_full, detection_width, detection_height)

    translations = [(0, 0)]
    cumulative_dx = 0
    cumulative_dy = 0
    cached_full_frames = [prev_full.astype(np.uint8)]

    if max_shift_px is not None:
        coarse_limit = int(round(max_shift_px * float(detection_width) / float(width)))
        fine_limit = int(round(max_shift_px))
        if coarse_limit <= 0:
            coarse_limit = None
        if fine_limit <= 0:
            fine_limit = None
    else:
        coarse_limit = None
        fine_limit = None

    ratio_x = float(width) / float(detection_width)
    ratio_y = float(height) / float(detection_height)
    for path in image_paths[1:]:
        try:
            curr_full = _load_png_frame(path)
        except Exception as exc:
            if logger:
                logger.warning('Unable to load frame %s for stabilization: %s',
                               path, exc)
            return [], []

        curr_height, curr_width = curr_full.shape
        if curr_width != full_width or curr_height != full_height:
            # Frames with inconsistent sizes cannot be stabilized together.
            return [], []

        if detection_width == curr_width and detection_height == curr_height:
            curr_small = curr_full.astype(np.float32)
        else:
            curr_small = _resize_grayscale_linear(curr_full, detection_width, detection_height)

        dx_small, dy_small = _phase_correlation_translation(
            prev_small,
            curr_small,
            max_allowed=coarse_limit,
        )
        approx_dx = dx_small * ratio_x
        approx_dy = dy_small * ratio_y

        window_radius = 3
        if fine_limit is not None and fine_limit < window_radius:
            window_radius = max(1, fine_limit)
        dx_full, dy_full = _refine_translation(
            prev_full,
            curr_full,
            approx_dx,
            approx_dy,
            max_allowed=fine_limit,
            window_radius=window_radius,
        )

        cumulative_dx += dx_full
        cumulative_dy += dy_full
        translations.append((cumulative_dx, cumulative_dy))

        prev_small = curr_small
        prev_full = curr_full
        cached_full_frames.append(curr_full.astype(np.uint8))

    return translations, cached_full_frames


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
                              crop_info, width, height, cached_frames=None):
    x_min, y_min, crop_w, crop_h = crop_info
    temp_dir = tempfile.mkdtemp(prefix='gwyddion_stabilized_')
    stabilized_paths = []
    use_cached = (
        cached_frames is not None and
        len(cached_frames) == len(image_paths)
    )
    for index, path in enumerate(image_paths):
        dx, dy = translations[index]
        crop_x = x_min - dx
        crop_y = y_min - dy
        crop_x = int(max(0, min(width - crop_w, crop_x)))
        crop_y = int(max(0, min(height - crop_h, crop_y)))
        output_path = os.path.join(temp_dir, 'frame_%06d.png' % index)
        if use_cached:
            frame_array = cached_frames[index]
        else:
            frame_array = _load_png_frame(path)
        cropped = frame_array[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        _write_png(output_path, cropped)
        stabilized_paths.append(output_path)
    return temp_dir, stabilized_paths


def _prepare_stabilized_sequence(image_paths, width, height, ffmpeg_path,
                                 stabilization, logger=None):
    if width is None or height is None:
        return None
    max_shift_px = _calculate_max_shift_pixels(stabilization, width)
    translations, cached_frames = _estimate_frame_translations(
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
        cached_frames=cached_frames,
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
