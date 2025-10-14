"""Core surface manipulation helpers used by the processing pipeline."""

from __future__ import annotations

from typing import Tuple

import numpy as np


def align_rows(surface: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Align rows of the surface by subtracting each row mean.

    The function returns the aligned surface and the per-row offsets that were
    removed. The offsets can be useful for logging or debugging.
    """

    if surface.ndim != 2:
        raise ValueError("Surface data must be a 2D array")

    row_offsets = surface.mean(axis=1, keepdims=True)
    aligned = surface - row_offsets
    return aligned, row_offsets.squeeze(axis=1)


def scale_surface(surface: np.ndarray, factor: float) -> np.ndarray:
    """Scale the surface using bilinear interpolation.

    Parameters
    ----------
    surface:
        The input 2D array.
    factor:
        Scaling factor applied to both axes. ``1.0`` leaves the surface
        unchanged.
    """

    if factor <= 0:
        raise ValueError("Scale factor must be positive")
    if np.isclose(factor, 1.0):
        return surface.copy()

    height, width = surface.shape
    new_height = max(1, int(round(height * factor)))
    new_width = max(1, int(round(width * factor)))

    y_idx = np.linspace(0, height - 1, new_height)
    x_idx = np.linspace(0, width - 1, new_width)

    y0 = np.floor(y_idx).astype(int)
    x0 = np.floor(x_idx).astype(int)
    y1 = np.clip(y0 + 1, 0, height - 1)
    x1 = np.clip(x0 + 1, 0, width - 1)

    wy = y_idx - y0
    wx = x_idx - x0

    Ia = surface[np.ix_(y0, x0)]
    Ib = surface[np.ix_(y0, x1)]
    Ic = surface[np.ix_(y1, x0)]
    Id = surface[np.ix_(y1, x1)]

    wa = (1 - wy)[:, None] * (1 - wx)[None, :]
    wb = (1 - wy)[:, None] * wx[None, :]
    wc = wy[:, None] * (1 - wx)[None, :]
    wd = wy[:, None] * wx[None, :]

    return wa * Ia + wb * Ib + wc * Ic + wd * Id


def remove_scars(surface: np.ndarray, kernel_size: int = 5, sigma_threshold: float = 3.0) -> np.ndarray:
    """Remove scars from the surface using a robust median filter approach.

    Parameters
    ----------
    surface:
        The input 2D array.
    kernel_size:
        Size of the median window evaluated horizontally. Must be an odd value.
    sigma_threshold:
        Multiple of the robust deviation used to detect outliers.
    """

    if kernel_size % 2 == 0:
        raise ValueError("Kernel size must be odd")

    pad = kernel_size // 2
    padded = np.pad(surface, ((0, 0), (pad, pad)), mode="edge")
    windows = [padded[:, i : i + surface.shape[1]] for i in range(kernel_size)]
    stack = np.stack(windows, axis=-1)
    median = np.median(stack, axis=-1)

    # Estimate deviation per row using the median absolute deviation.
    diff = surface - median
    mad = np.median(np.abs(diff), axis=1, keepdims=True)
    robust_sigma = 1.4826 * (mad + 1e-9)

    corrected = surface.copy()
    mask = np.abs(diff) > (sigma_threshold * robust_sigma)
    corrected[mask] = median[mask]
    return corrected
