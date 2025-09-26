# Gwyddion Batch Processing Utilities

This repository provides reusable building blocks for processing AFM/SPM images
with the [Gwyddion](http://gwyddion.net/) Python bindings.  The utilities remain
fully compatible with Python 2.7 (the environment recommended by Gwyddion) while
also running on modern Python interpreters.  The original monolithic script has
been refactored into a small, testable package with both API and command-line
entry points.

## Package layout

```
gwyddion_batch/
├── __init__.py            # Convenience exports
├── cli.py                 # argparse-based command line interface
├── config.py              # Configuration helpers and validation
├── gwyddion_loader.py     # Utilities for importing the gwy module
├── processor.py           # Core processing logic
└── video.py               # ffmpeg-based video stitching helpers
```

Helper scripts in the repository demonstrate how the API can be consumed from
Python code while preserving the editable "user settings" block from the
original script:

* `run_batch.py` runs the full image processing workflow (optionally including
  video creation).
* `render_video.py` stitches already-processed images into a video without
  reprocessing the raw SPM data.

## Installation

The code is intentionally lightweight and has no external dependencies beyond
Python and the Gwyddion bindings.  Copy the package into a location on your
`PYTHONPATH` or install it into a virtual environment using `pip`:

```bash
pip install .
```

(You can also run it directly from the repository without installation.)

## Usage

### Python API

```python
from gwyddion_batch import BatchConfig, GwyddionBatchProcessor, import_gwyddion

config = BatchConfig(
    folder_path=r"D:\\Data\\MyExperiment",
    channel_number=0,
    pixel_count=1024,
)

gwy = import_gwyddion(config.gwyddion_paths)
processor = GwyddionBatchProcessor(gwy)
result = processor.process_folder(config)
print('Processed images saved to', result['output_directory'])
if result.get('video_path'):
    print('Video created at', result['video_path'])
```

### Command line interface

```bash
python -m gwyddion_batch.cli D:\Data\MyExperiment --pixels 1024 --channel 0
```

Use `--help` for the full list of options.  Additional flags let you choose the
output directory, enable automatic video rendering (requires `ffmpeg`), and
turn on frame stabilization with cropping to the shared overlap of all frames.

### Video stitching

Processed images are written to a dedicated folder (``processed`` by default).
When video rendering is enabled the tool will invoke `ffmpeg` using a concat
file similar to the batch scripts provided previously.  You can customise the
frame rate, per-frame duration, pixel format, and pass through additional
arguments to `ffmpeg`.

Enable the new stabilization option to perform a two-pass `ffmpeg` run using
``vidstab`` filters.  The detection pass measures per-frame drift, the
transformation pass applies the correction, and the output is cropped to the
shared image area to avoid edge artifacts.  CLI flags (``--stabilize``,
``--stabilize-shakiness`` and friends) map directly to the constants exposed in
`run_batch.py` and `render_video.py`.

### Example scripts

The `run_batch.py` script keeps the editable constants approach of the original
script while delegating the heavy lifting to the reusable package.  Adjust the
constants at the top of the file and execute it with Python:

```bash
python run_batch.py
```

The script exposes explicit constants for the output subdirectory, video
rendering options, and stabilization parameters, mirroring the available
command line flags.

`render_video.py` offers the same configurable pattern for stitching images
that have already been processed.  Point the script at your processed image
folder, enable stabilization if desired, and run it directly:

```bash
python render_video.py
```

## Development

Run a basic syntax check with:

```bash
python -m compileall gwyddion_batch run_batch.py render_video.py
```

This ensures all modules are syntactically correct without requiring the
Gwyddion libraries at compile time.
