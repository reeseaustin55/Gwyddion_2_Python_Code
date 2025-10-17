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
for channel, videos in sorted(result.get('video_paths', {}).items()):
    if isinstance(videos, dict):
        for kind, outputs in sorted(videos.items()):
            if isinstance(outputs, dict):
                for direction, path in sorted(outputs.items()):
                    print('Channel %d %s %s video -> %s'
                          % (channel, kind, direction, path))
            else:
                print('Channel %d %s video -> %s' % (channel, kind, outputs))
    else:
        print('Channel %d video created at %s' % (channel, videos))
```

### Command line interface

```bash
python -m gwyddion_batch.cli D:\Data\MyExperiment --pixels 1024 --channel 0 --channel 1
```

Use `--help` for the full list of options.  Additional flags let you choose the
output directory, enable automatic video rendering (requires `ffmpeg`), set the
target video duration (which controls the playback speed multiplier embedded in
the filename), adjust the constant playback rate with `--video-fps` (default
`30`), render
alternating UP/DOWN scan videos with `--split-scans`, and turn on frame
stabilization with cropping to the shared overlap of all frames.  The `--filter`
flag defaults to `.ibw` so the raw Bruker files are processed without picking up
unrelated data.  Image-processing steps can now be toggled from the CLI as well:
`--no-flatten`, `--no-align-rows`, `--align-method`, `--align-degree`,
`--remove-scars`, `--no-fix-zero`, `--stats`, and `--acf` mirror the GUI
checkboxes so scripted runs match interactive sessions.

### Video stitching

Processed images are written to a timestamped subfolder inside the selected data
directory (for example ``output_20250318_143512``).  The GUI now refreshes this
timestamp every time batch processing starts so consecutive runs never overwrite
previous results, and the folder is always created beneath the chosen data
directory.  Each channel now receives
its own nested directory (``channel0``, ``channel1`` and so on) and, when ACF
generation is enabled, the autocorrelation images for that channel are grouped
under an ``acf`` subfolder.  Filenames still include the channel number so
multiple channels can be exported from the same source file without collisions.
An additional toggle exports PSDF imagery to a sibling ``psdf`` folder with a
configurable zoom factor (choices ``1×``, ``2×``, ``4×``, ``8×`` or ``16×``)
so the generated view matches the interactive Gwyddion workflow while keeping
the exported image at the same pixel dimensions as the main channel output.

When video rendering is enabled the tool invokes `ffmpeg` using a concat file
similar to the batch scripts provided previously.  The time span between the
first and last source ``.ibw`` file is divided by the requested video duration to
compute a playback speed multiplier such as ``4X``; that multiplier is appended
to each rendered video filename.  Capture timestamps are honoured by default so
frame durations reflect the original acquisition cadence while duplicated frames
maintain a constant 30 fps playback.  The new `--uniform-frame-duration` flag
(and GUI checkbox) skips the timestamp logic and gives every frame the same
exposure, which can help when capture metadata is sparse.  Supplying
`--video-fps 0` (or an explicit value) lets advanced users fall back to
pure-duration behaviour or pick a different constant rate.  Enabling
`--split-scans` (or the corresponding GUI checkbox) additionally produces UP and
DOWN scan videos generated from alternating frames for both the processed data
and any ACF imagery.

Enable the stabilization option to analyse consecutive frames directly inside
the Python pipeline.  The processor downsamples each image, subtracts adjacent
pairs to search for the translation with the smallest absolute difference,
accumulates the measured offsets, and crops the shared overlap so the exported
video only includes regions present in every frame.  The CLI pairs
``--stabilize`` with a single ``--stabilize-max-percent`` flag (mirrored in the
GUI) to specify the maximum frame-to-frame displacement as a percentage of the
frame width; everything else is handled automatically.

When statistics export is enabled the processor calls Gwyddion's own statistical
quantities module and writes the complete set of reported values (Sa, Sq, hybrid
metrics, scan-line discrepancy, and more) — including their reported units — to
a companion `*_stats.txt` file in the channel directory.  ACF generation adds an
additional `*_acf.png` rendered from the processed data so each selected channel
produces both the cleaned height image and its autocorrelation counterpart,
organised beneath the channel folder.

### Example scripts

The `run_batch.py` script keeps the editable constants approach of the original
script while delegating the heavy lifting to the reusable package.  Adjust the
constants (including the list of channel numbers and the new processing toggles)
at the top of the file and execute it with Python:

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
the per-frame durations to fit the requested video length, and, by default,
duplicates frames to play back at 30 fps.  Supplying `--video-fps 0` switches to
pure duration-driven playback, while any other explicit value replaces the
default rate.  Command line flags let you override the glob pattern, ffmpeg
path, stabilization drift allowance, source data folder, frame rate, and output
filename as needed:

```bash
python render_video.py
python render_video.py --images "D:\\AFM Images\\Video Processing\\Set1\\processed" --stabilize
```

### Windows batch helper

If you prefer the original Windows batch workflow, the repository now ships
with `render_processed_videos.bat` alongside a companion PowerShell helper.
Update the variables at the top of the `.bat` file (processed image directory,
raw `.ibw` folder, desired video duration, ffmpeg path, and whether to emit
separate UP/DOWN scan videos or ACF videos) and run it from a Windows command
prompt.  The batch file hands off to `render_processed_videos.ps1`, which
re-creates the concat manifests, duplicates frames to enforce a constant frame
rate, computes the capture-span multiplier for the filenames, and calls ffmpeg
with the same even-dimension safeguards as the Python tooling.  Because the
PowerShell script reads the `.ibw` timestamps directly it keeps the playback
speed consistent with the batch processor.

## Development

Run a basic syntax check with:

```bash
python -m compileall gwyddion_batch run_batch.py run_batch_gui.py render_video.py
```

This ensures all modules are syntactically correct without requiring the
Gwyddion libraries at compile time.

Prefer a graphical workflow?  Launch `run_batch_gui.py` to pick the folder,
channels, pixel count, optional video duration, and stabilization controls
through a Tkinter interface.  The folder selector starts
in `D:\AFM Images` and automatically proposes a timestamped
`output_YYYYMMDD_HHMMSS` subdirectory so each run lands in its own folder.  New
checkboxes let you toggle flattening, row alignment (median of differences or a
configurable polynomial degree), scar removal, height-only zero fixing, stats
export, and ACF generation.  The GUI also remembers the last settings you used
so reopening the tool restores your most recent inputs.
