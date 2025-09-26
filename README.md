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
├── gui.py                 # Tkinter GUI for interactive configuration
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
* `run_batch_gui.py` launches the GUI for selecting folders, channels, and
  other user-configurable settings.

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
    channel_numbers=[0, 1],
    pixel_count=1024,
)

gwy = import_gwyddion(config.gwyddion_paths)
processor = GwyddionBatchProcessor(gwy)
result = processor.process_folder(config)
print('Processed images saved to', result['output_directory'])
for channel, details in sorted(result.get('per_channel', {}).items()):
    print('Channel %d -> %d/%d images' % (channel, details['processed'], details['total']))
for channel, video_path in sorted(result.get('video_paths', {}).items()):
    print('Channel %d video created at %s' % (channel, video_path))
```

### Command line interface

```bash
python -m gwyddion_batch.cli D:\Data\MyExperiment --pixels 1024 --channel 0 --channel 1
```

Use `--help` for the full list of options.  Additional flags let you choose the
output directory, enable automatic video rendering (requires `ffmpeg`), set the
target video duration (which controls the playback speed multiplier embedded in
the filename), and turn on frame stabilization with cropping to the shared
overlap of all frames.  The `--filter` flag defaults to `.ibw` so the raw Bruker
files are processed without picking up unrelated data.

### Video stitching

Processed images are written to a timestamped subfolder inside the selected data
directory (for example ``outputs_20250318_143512``).  Each processed PNG
filename still includes the channel number so multiple channels can be exported
from the same source file without collisions.  When video rendering is enabled
the tool will invoke `ffmpeg` using a concat file similar to the batch scripts
provided previously.  The time span between the first and last source ``.ibw``
file is divided by the requested video duration to compute a playback speed
multiplier such as ``4X``; that multiplier is appended to the video filename.  Per
frame durations are scaled automatically so the final video matches the
requested length.

Enable the new stabilization option to perform a two-pass `ffmpeg` run using
``vidstab`` filters.  The detection pass measures per-frame drift, the
transformation pass applies the correction, and the output is cropped to the
shared image area to avoid edge artifacts.  CLI flags (``--stabilize``,
``--stabilize-shakiness`` and friends) map directly to the constants exposed in
`run_batch.py` and `render_video.py`.

### Example scripts

The `run_batch.py` script keeps the editable constants approach of the original
script while delegating the heavy lifting to the reusable package.  Adjust the
constants (including the list of channel numbers) at the top of the file and
execute it with Python:

```bash
python run_batch.py
```

The script exposes explicit constants for the default filters, optional video
duration, and stabilization parameters.  Leaving `OUTPUT_SUBDIR` set to `None`
lets the processor create the timestamped output folder automatically.

`render_video.py` offers the same configurable pattern for stitching images
that have already been processed.  When launched without arguments it opens a
folder selection dialog (defaulting to `D:\AFM Images`) so you can point it at
your processed frames, even on Python 2.7.  The script reads the modification
times of the corresponding `.ibw` files to determine the capture span, scales
the per-frame durations to fit the requested video length, and appends the
resulting multiplier to the output filename.  Command line flags let you
override the glob pattern, ffmpeg path, stabilization parameters, source data
folder, and output filename as needed:

```bash
python render_video.py
python render_video.py --images "D:\\AFM Images\\Video Processing\\Set1\\processed" --stabilize
```

## Development

Run a basic syntax check with:

```bash
python -m compileall gwyddion_batch run_batch.py run_batch_gui.py render_video.py
```

This ensures all modules are syntactically correct without requiring the
Gwyddion libraries at compile time.

Prefer a graphical workflow?  Launch `run_batch_gui.py` to pick the folder,
channels, pixel count, optional video duration, and stabilization controls
through a Tkinter interface.  The folder selector starts in `D:\AFM Images` and
automatically proposes a timestamped subdirectory for the outputs so each run
lands in its own folder.
