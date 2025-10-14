"""Video rendering utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import imageio.v2 as imageio
import numpy as np

from .options import ProcessingOptions
from .stabilization import stabilise_if_requested


def _ensure_uint8(frame: np.ndarray) -> np.ndarray:
    if frame.dtype == np.uint8:
        return frame
    frame_min = float(frame.min())
    frame_max = float(frame.max())
    if frame_max - frame_min <= 0:
        return np.zeros_like(frame, dtype=np.uint8)
    normalised = (frame - frame_min) / (frame_max - frame_min)
    return (normalised * 255).astype(np.uint8)


def create_video(frames: Sequence[np.ndarray], options: ProcessingOptions) -> Path:
    """Create a video from ``frames`` honouring stabilisation options."""

    if not frames:
        raise ValueError("At least one frame is required to create a video")

    output_dir = options.ensure_output_directory()
    output_path = output_dir / options.video_filename

    processed_frames, _ = stabilise_if_requested(frames, options)

    # Always write the video, even when stabilisation is active.
    with imageio.get_writer(output_path, fps=options.fps) as writer:
        for frame in processed_frames:
            if frame.ndim == 2:
                writer.append_data(_ensure_uint8(frame))
            else:
                writer.append_data(_ensure_uint8(frame[:, :, 0]))

    return output_path
