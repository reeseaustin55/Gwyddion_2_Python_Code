"""Processing options for the Gwyddion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProcessingOptions:
    """Configuration values that control the processing pipeline.

    Attributes
    ----------
    remove_scars:
        If ``True`` the pipeline runs scar removal before and after scaling.
    scale_factor:
        Multiplicative scale to apply to the surface. ``1.0`` keeps the
        original resolution.
    generate_psdf:
        When enabled a 2D Power Spectral Density Function image is generated.
    generate_angular_spectrum:
        When enabled the angular spectrum derived from the PSDF is exported.
    enable_stabilization:
        Enables video frame stabilization prior to encoding.
    stabilization_max_displacement_percent:
        The maximum expected displacement between consecutive frames expressed
        as a percentage of the frame width.
    output_directory:
        Directory where artefacts (PSDF images, spectra, videos) are written.
    video_filename:
        Name of the generated video file.
    fps:
        Frames per second for the video output.
    """

    remove_scars: bool = False
    scale_factor: float = 1.0
    generate_psdf: bool = False
    generate_angular_spectrum: bool = False
    enable_stabilization: bool = False
    stabilization_max_displacement_percent: float = 5.0
    output_directory: Path = field(default_factory=lambda: Path("outputs"))
    video_filename: str = "output.mp4"
    fps: int = 30

    def ensure_output_directory(self) -> Path:
        """Create the configured output directory if it does not already exist."""

        self.output_directory.mkdir(parents=True, exist_ok=True)
        return self.output_directory

    def max_pixel_displacement(self, frame_width: int) -> float:
        """Return the max displacement in pixels allowed for stabilization.

        Parameters
        ----------
        frame_width:
            Width of the frame in pixels used to convert from percentage to
            absolute pixel displacement.
        """

        return (self.stabilization_max_displacement_percent / 100.0) * frame_width
