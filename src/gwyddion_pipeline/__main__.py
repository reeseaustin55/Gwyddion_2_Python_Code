"""Command line interface for the processing pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

import imageio.v2 as imageio
import numpy as np

from .options import ProcessingOptions
from .pipeline import process_surface
from .video import create_video


def _load_surface(path: Path) -> np.ndarray:
    data = np.load(path)
    if data.ndim != 2:
        raise ValueError("Surface data must be 2D")
    return data.astype(float)


def _load_frames(paths: List[Path]) -> List[np.ndarray]:
    frames: List[np.ndarray] = []
    for path in paths:
        frame = imageio.imread(path)
        if frame.ndim == 3:
            frame = frame[..., 0]
        frames.append(frame.astype(float))
    return frames


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gwyddion surface processing pipeline")
    parser.add_argument("surface", type=Path, help="Path to a .npy surface file")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale factor")
    parser.add_argument("--remove-scars", action="store_true", help="Enable scar removal")
    parser.add_argument("--psdf", action="store_true", help="Generate PSDF image")
    parser.add_argument("--angular-spectrum", action="store_true", help="Export angular spectrum")
    parser.add_argument("--frames", type=Path, nargs="*", help="Optional sequence of frames for video generation")
    parser.add_argument("--stabilize", action="store_true", help="Enable video stabilization")
    parser.add_argument(
        "--max-displacement",
        type=float,
        default=5.0,
        help="Maximum displacement between frames as percentage of frame width",
    )
    parser.add_argument("--output", type=Path, default=Path("outputs"), help="Output directory")
    parser.add_argument("--video-name", type=str, default="output.mp4", help="Video filename")
    parser.add_argument("--fps", type=int, default=30, help="Video frame rate")

    args = parser.parse_args(argv)

    options = ProcessingOptions(
        remove_scars=args.remove_scars,
        scale_factor=args.scale,
        generate_psdf=args.psdf,
        generate_angular_spectrum=args.angular_spectrum,
        enable_stabilization=args.stabilize,
        stabilization_max_displacement_percent=args.max_displacement,
        output_directory=args.output,
        video_filename=args.video_name,
        fps=args.fps,
    )

    surface = _load_surface(args.surface)
    result = process_surface(surface, options)

    if args.frames:
        frames = _load_frames(list(args.frames))
        create_video(frames, options)

    print("Processing complete.")
    print("Metadata keys:", ", ".join(result.metadata.keys()))
    if result.artefacts:
        print("Generated artefacts:")
        for artefact in result.artefacts:
            print(" -", artefact)


if __name__ == "__main__":  # pragma: no cover
    main()
