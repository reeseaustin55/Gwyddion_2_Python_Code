"""Utilities for computing PSDF and angular spectrum artefacts."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict

import numpy as np


def _hann_window(shape: tuple[int, int]) -> np.ndarray:
    """Return a 2D Hann window to reduce edge artefacts."""

    y_window = np.hanning(shape[0])[:, None]
    x_window = np.hanning(shape[1])[None, :]
    return y_window * x_window


def compute_psdf(surface: np.ndarray) -> np.ndarray:
    """Compute the two-dimensional power spectral density function."""

    if surface.ndim != 2:
        raise ValueError("Surface must be 2D")

    surface = surface - np.mean(surface)
    window = _hann_window(surface.shape)
    windowed = surface * window

    spectrum = np.fft.fftshift(np.fft.fft2(windowed))
    psdf = np.abs(spectrum) ** 2
    return psdf


def save_psdf_image(psdf: np.ndarray, path: Path) -> None:
    """Persist the PSDF as a log-normalised greyscale PNG."""

    import imageio.v2 as imageio

    log_psdf = np.log10(psdf + 1e-12)
    log_psdf -= log_psdf.min()
    if log_psdf.max() > 0:
        log_psdf /= log_psdf.max()
    image = (log_psdf * 255).astype(np.uint8)
    imageio.imwrite(path, image)


def compute_angular_spectrum(psdf: np.ndarray, bins: int = 360) -> Dict[str, np.ndarray]:
    """Compute the angular power spectrum from a PSDF."""

    if psdf.ndim != 2:
        raise ValueError("PSDF must be 2D")

    height, width = psdf.shape
    y = np.arange(height) - height / 2
    x = np.arange(width) - width / 2
    X, Y = np.meshgrid(x, y)
    angles = (np.degrees(np.arctan2(Y, X)) + 360) % 360

    flat_angles = angles.ravel()
    flat_power = psdf.ravel()

    bin_edges = np.linspace(0, 360, bins + 1)
    spectrum = np.zeros(bins, dtype=float)

    for idx in range(bins):
        mask = (flat_angles >= bin_edges[idx]) & (flat_angles < bin_edges[idx + 1])
        if np.any(mask):
            spectrum[idx] = float(np.mean(flat_power[mask]))
        else:
            spectrum[idx] = 0.0

    angle_centres = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    return {"angle": angle_centres, "power": spectrum}


def save_angular_spectrum(spectrum: Dict[str, np.ndarray], path: Path) -> None:
    """Write the angular spectrum as a CSV file."""

    with path.open("w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["angle_deg", "power"])
        for angle, power in zip(spectrum["angle"], spectrum["power"]):
            writer.writerow([f"{angle:.2f}", f"{power:.6e}"])
