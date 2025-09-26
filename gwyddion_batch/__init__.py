"""Convenience exports for the Gwyddion batch processing package."""

from __future__ import absolute_import

from .config import BatchConfig, VideoSettings, StabilizationSettings, format_time_multiplier
from .processor import GwyddionBatchProcessor, get_supported_extensions, generate_output_path
from .gwyddion_loader import import_gwyddion
from .video import stitch_images_to_video

try:  # GUI support requires Tkinter which may be unavailable on some systems
    from .gui import BatchProcessorGUI
except Exception:  # pragma: no cover - optional dependency
    BatchProcessorGUI = None

__all__ = [
    'BatchConfig',
    'GwyddionBatchProcessor',
    'get_supported_extensions',
    'generate_output_path',
    'import_gwyddion',
    'VideoSettings',
    'StabilizationSettings',
    'stitch_images_to_video',
    'format_time_multiplier',
]

if BatchProcessorGUI is not None:
    __all__.append('BatchProcessorGUI')
