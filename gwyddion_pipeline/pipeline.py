"""High-level orchestration utilities for processing Gwyddion datasets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import data_processing
from .data_processing import AngularSpectrum, Matrix
from .stabilization import FrameStabilizer


@dataclass
class OutputPaths:
    psdf_image: Optional[str] = None
    angular_spectrum_csv: Optional[str] = None
    video: Optional[str] = None


@dataclass
class ProcessingOptions:
    remove_scars: bool = False
    scale_factor: float = 1.0
    generate_psdf: bool = False
    generate_angular_spectrum: bool = False
    enable_stabilisation: bool = False
    max_frame_displacement_pct: float = 5.0


@dataclass
class ProcessedResult:
    data: Matrix
    psdf: Optional[Matrix] = None
    angular_spectrum: Optional[AngularSpectrum] = None


class GwyddionProcessor:
    def __init__(self, options: ProcessingOptions, outputs: Optional[OutputPaths] = None) -> None:
        self.options = options
        self.outputs = outputs or OutputPaths()
        self._stabiliser = FrameStabilizer()

    def process_data(self, data: Sequence[Sequence[float]]) -> ProcessedResult:
        working: Matrix = data_processing.align_rows(data)
        if self.options.remove_scars:
            working = data_processing.remove_scars(working)

        if self.options.scale_factor != 1.0:
            working = data_processing.scale_data(working, self.options.scale_factor)

        if self.options.remove_scars:
            working = data_processing.remove_scars(working)
            working = data_processing.align_rows(working)

        psdf: Optional[Matrix] = None
        spectrum: Optional[AngularSpectrum] = None
        if self.options.generate_psdf or self.options.generate_angular_spectrum:
            psdf = data_processing.compute_psdf(working)

        if self.options.generate_psdf and psdf and self.outputs.psdf_image:
            data_processing.save_psdf_image(psdf, self.outputs.psdf_image)

        if self.options.generate_angular_spectrum and psdf:
            spectrum = data_processing.compute_angular_spectrum(psdf)
            if self.outputs.angular_spectrum_csv:
                data_processing.save_angular_spectrum(spectrum, self.outputs.angular_spectrum_csv)

        return ProcessedResult(data=working, psdf=psdf, angular_spectrum=spectrum)

    def stabilise_frames(self, frames: Sequence[Sequence[Sequence[float]]]) -> List[List[List[float]]]:
        if not self.options.enable_stabilisation:
            return [list(map(float, row)) for row in frames]  # type: ignore[arg-type]
        max_pct = max(0.0, float(self.options.max_frame_displacement_pct))
        return self._stabiliser.stabilise(frames, max_pct)

    def write_video(self, frames: Sequence[Sequence[Sequence[float]]]) -> None:
        if self.outputs.video is None:
            return
        final_frames = self.stabilise_frames(frames)
        self._stabiliser.write_video(final_frames, self.outputs.video)

