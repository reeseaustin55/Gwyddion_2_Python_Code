"""Core processing utilities inspired by Gwyddion operations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import cmath
import math

Matrix = List[List[float]]


@dataclass(frozen=True)
class AngularSpectrum:
    """Angular spectrum information derived from a 2D power spectral density."""

    angles_deg: List[float]
    intensity: List[float]

    def as_rows(self) -> Iterable[Tuple[float, float]]:
        for angle, value in zip(self.angles_deg, self.intensity):
            yield angle, value


def _copy_matrix(data: Sequence[Sequence[float]]) -> Matrix:
    return [list(row) for row in data]


def _matrix_shape(data: Sequence[Sequence[float]]) -> Tuple[int, int]:
    rows = len(data)
    cols = len(data[0]) if rows else 0
    return rows, cols


def _roll_row(row: Sequence[float], shift: int) -> List[float]:
    size = len(row)
    shift = shift % size
    return list(row[-shift:] + row[:-shift]) if shift else list(row)


def align_rows(data: Sequence[Sequence[float]]) -> Matrix:
    """Align rows by maximising direct correlation with the first row."""

    rows, cols = _matrix_shape(data)
    if rows == 0 or cols == 0:
        return _copy_matrix(data)

    reference = list(data[0])
    aligned = [reference]

    for row_index in range(1, rows):
        row = list(data[row_index])
        best_shift = 0
        best_score = float("-inf")
        for shift in range(-cols + 1, cols):
            rolled = _roll_row(row, shift)
            score = sum(r * ref for r, ref in zip(rolled, reference))
            if score > best_score:
                best_score = score
                best_shift = shift
        aligned.append(_roll_row(row, best_shift))

    return aligned


def remove_scars(
    data: Sequence[Sequence[float]], *, kernel_size: int = 5, threshold: float = 2.5
) -> Matrix:
    if kernel_size % 2 == 0:
        raise ValueError("kernel_size must be odd")

    rows, cols = _matrix_shape(data)
    filtered = _copy_matrix(data)
    half = kernel_size // 2

    for row_index in range(rows):
        for col_index in range(cols):
            window = []
            for offset in range(-half, half + 1):
                neighbour = col_index + offset
                if 0 <= neighbour < cols:
                    window.append(data[row_index][neighbour])
            if len(window) < 2:
                continue
            median = _median(window)
            deviation = _stddev(window)
            if deviation == 0:
                deviation = 1.0
            value = data[row_index][col_index]
            if abs(value - median) > threshold * deviation:
                filtered[row_index][col_index] = median

    return filtered


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _stddev(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def scale_data(data: Sequence[Sequence[float]], scale_factor: float) -> Matrix:
    return [[value * scale_factor for value in row] for row in data]


def _dft(signal: Sequence[complex], inverse: bool = False) -> List[complex]:
    length = len(signal)
    if length == 0:
        return []
    result: List[complex] = []
    factor = 2j * math.pi / length
    if inverse:
        factor *= -1
    for k in range(length):
        total = 0j
        for n, value in enumerate(signal):
            total += value * cmath.exp(-factor * k * n)
        if inverse:
            total /= length
        result.append(total)
    return result


def _fft2(data: Sequence[Sequence[float]]) -> List[List[complex]]:
    rows, cols = _matrix_shape(data)
    if rows == 0 or cols == 0:
        return []

    row_transforms = []
    for row in data:
        row_complex = [complex(value, 0.0) for value in row]
        row_transforms.append(_dft(row_complex))

    transformed: List[List[complex]] = []
    for u in range(rows):
        column = [row_transforms[row][u] for row in range(rows)]
        transformed.append(_dft(column))

    return [list(column[:cols]) for column in transformed]


def compute_psdf(data: Sequence[Sequence[float]]) -> Matrix:
    spectrum = _fft2(data)
    if not spectrum:
        return []
    rows = len(spectrum)
    cols = len(spectrum[0])
    psdf: Matrix = [[0.0 for _ in range(cols)] for _ in range(rows)]
    for row in range(rows):
        for col in range(cols):
            value = spectrum[row][col]
            psdf[row][col] = (value.real ** 2 + value.imag ** 2)
    return _fftshift(psdf)


def _fftshift(data: Matrix) -> Matrix:
    rows, cols = _matrix_shape(data)
    shifted = [[0.0 for _ in range(cols)] for _ in range(rows)]
    row_mid = rows // 2
    col_mid = cols // 2
    for row in range(rows):
        for col in range(cols):
            new_row = (row + row_mid) % rows
            new_col = (col + col_mid) % cols
            shifted[new_row][new_col] = data[row][col]
    return shifted


def compute_angular_spectrum(psdf: Matrix, n_bins: int = 180) -> AngularSpectrum:
    rows, cols = _matrix_shape(psdf)
    if rows == 0 or cols == 0:
        return AngularSpectrum([], [])

    center_row = (rows - 1) / 2.0
    center_col = (cols - 1) / 2.0

    angles = [i * (180.0 / n_bins) for i in range(n_bins)]
    totals = [0.0 for _ in range(n_bins)]
    counts = [0 for _ in range(n_bins)]

    for row in range(rows):
        for col in range(cols):
            magnitude = psdf[row][col]
            if magnitude == 0:
                continue
            dy = row - center_row
            dx = col - center_col
            angle = math.degrees(math.atan2(dy, dx))
            angle = abs(angle)
            if angle >= 180.0:
                angle -= 180.0
            index = int(angle / 180.0 * n_bins) % n_bins
            totals[index] += magnitude
            counts[index] += 1

    intensities = [totals[i] / counts[i] if counts[i] else 0.0 for i in range(n_bins)]
    return AngularSpectrum(angles_deg=angles, intensity=intensities)


def normalise_psdf(psdf: Matrix) -> Matrix:
    rows, cols = _matrix_shape(psdf)
    if rows == 0 or cols == 0:
        return []
    min_val = min(min(row) for row in psdf)
    max_val = max(max(row) for row in psdf)
    if max_val == min_val:
        return [[0.0 for _ in range(cols)] for _ in range(rows)]
    return [[(value - min_val) / (max_val - min_val) for value in row] for row in psdf]


def save_psdf_image(psdf: Matrix, path: str) -> None:
    normalised = normalise_psdf(psdf)
    rows, cols = _matrix_shape(normalised)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"P2\n{cols} {rows}\n255\n")
        for row in normalised:
            values = [str(int(value * 255)) for value in row]
            handle.write(" ".join(values) + "\n")


def save_angular_spectrum(spectrum: AngularSpectrum, path: str) -> None:
    header = "angle_deg,intensity"
    rows = [f"{angle:.6f},{value:.6f}" for angle, value in spectrum.as_rows()]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n")
        handle.write("\n".join(rows))
