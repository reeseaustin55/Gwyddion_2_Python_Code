"""Processing pipeline coordinating surface operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .operations import align_rows, remove_scars, scale_surface
from .options import ProcessingOptions
from .psd import compute_psdf, compute_angular_spectrum, save_psdf_image, save_angular_spectrum


@dataclass
class PipelineResult:
    """Result of running the surface processing pipeline."""

    surface: np.ndarray
    metadata: Dict[str, np.ndarray]
    artefacts: List[Path]


def process_surface(surface: np.ndarray, options: ProcessingOptions) -> PipelineResult:
    """Process ``surface`` according to ``options``.

    The implementation follows the requested ordering rules for scar removal:

    * Align rows is always executed first when available.
    * When scar removal is enabled, it runs immediately after row alignment,
      again after scaling, and an additional alignment is performed after each
      scar removal pass.
    """

    if surface.ndim != 2:
        raise ValueError("Surface data must be a 2D array")

    artefacts: List[Path] = []
    metadata: Dict[str, np.ndarray] = {}

    processed = surface.astype(float, copy=True)

    # Initial alignment
    processed, row_offsets = align_rows(processed)
    metadata["row_offsets_initial"] = row_offsets

    # First scar removal (if enabled) and re-alignment
    if options.remove_scars:
        processed = remove_scars(processed)
        processed, offsets_after_scars = align_rows(processed)
        metadata["row_offsets_after_initial_scars"] = offsets_after_scars

    # Scaling
    if not np.isclose(options.scale_factor, 1.0):
        processed = scale_surface(processed, options.scale_factor)
        metadata["scale_factor"] = np.array([options.scale_factor])

    # Post-scale alignment prior to a second scar removal
    if options.remove_scars:
        processed, offsets_before_second_scars = align_rows(processed)
        metadata["row_offsets_before_second_scars"] = offsets_before_second_scars
        processed = remove_scars(processed)
        processed, offsets_after_second_scars = align_rows(processed)
        metadata["row_offsets_after_second_scars"] = offsets_after_second_scars
    else:
        processed, row_offsets_after_scale = align_rows(processed)
        metadata["row_offsets_after_scale"] = row_offsets_after_scale

    output_dir = options.ensure_output_directory()

    if options.generate_psdf or options.generate_angular_spectrum:
        psdf = compute_psdf(processed)
        metadata["psdf_shape"] = np.array(psdf.shape)

        if options.generate_psdf:
            psdf_path = output_dir / "psdf.png"
            save_psdf_image(psdf, psdf_path)
            artefacts.append(psdf_path)

        if options.generate_angular_spectrum:
            spectrum_path = output_dir / "angular_spectrum.csv"
            spectrum = compute_angular_spectrum(psdf)
            save_angular_spectrum(spectrum, spectrum_path)
            artefacts.append(spectrum_path)

    return PipelineResult(surface=processed, metadata=metadata, artefacts=artefacts)
