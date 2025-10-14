"""Frame stabilisation helpers."""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

import numpy as np

from .options import ProcessingOptions


ArrayLike = np.ndarray


def _phase_correlation(reference: ArrayLike, frame: ArrayLike) -> Tuple[float, float]:
    """Estimate the translation between ``reference`` and ``frame``."""

    ref_fft = np.fft.fft2(reference)
    frame_fft = np.fft.fft2(frame)
    cross_power = ref_fft * np.conjugate(frame_fft)
    cross_power /= np.abs(cross_power) + 1e-12
    correlation = np.fft.ifft2(cross_power)
    maxima = np.unravel_index(np.argmax(np.abs(correlation)), correlation.shape)

    shifts = np.array(maxima, dtype=float)
    for axis, size in enumerate(reference.shape):
        if shifts[axis] > size / 2:
            shifts[axis] -= size
    dy, dx = shifts
    return dy, dx


def _apply_integer_shift(frame: ArrayLike, dy: int, dx: int) -> ArrayLike:
    """Apply an integer translation with zero padding."""

    shifted = np.zeros_like(frame)
    height, width = frame.shape

    y_start_src = max(0, -dy)
    y_end_src = min(height, height - dy)
    x_start_src = max(0, -dx)
    x_end_src = min(width, width - dx)

    y_start_dst = max(0, dy)
    y_end_dst = y_start_dst + (y_end_src - y_start_src)
    x_start_dst = max(0, dx)
    x_end_dst = x_start_dst + (x_end_src - x_start_src)

    if y_end_dst > y_start_dst and x_end_dst > x_start_dst:
        shifted[y_start_dst:y_end_dst, x_start_dst:x_end_dst] = frame[
            y_start_src:y_end_src, x_start_src:x_end_src
        ]
    return shifted


def stabilise_sequence(frames: Sequence[ArrayLike], options: ProcessingOptions) -> Tuple[List[ArrayLike], List[Tuple[int, int]]]:
    """Return stabilised frames and the applied shifts."""

    if not frames:
        return [], []

    reference = frames[0]
    height, width = reference.shape
    max_shift = int(round(options.max_pixel_displacement(width)))

    stabilised = [reference.copy()]
    applied_shifts: List[Tuple[int, int]] = [(0, 0)]

    for frame in frames[1:]:
        dy, dx = _phase_correlation(reference, frame)
        dy = int(np.clip(np.round(dy), -max_shift, max_shift))
        dx = int(np.clip(np.round(dx), -max_shift, max_shift))
        shifted = _apply_integer_shift(frame, dy, dx)
        stabilised.append(shifted)
        applied_shifts.append((dy, dx))
    return stabilised, applied_shifts


def stabilise_if_requested(frames: Sequence[ArrayLike], options: ProcessingOptions) -> Tuple[List[ArrayLike], List[Tuple[int, int]]]:
    """Stabilise frames when the option is enabled."""

    if not options.enable_stabilization:
        return list(frames), [(0, 0)] * len(frames)
    return stabilise_sequence(frames, options)


# Backwards compatible alias with previous American spelling.
stabilize_frames = stabilise_if_requested
