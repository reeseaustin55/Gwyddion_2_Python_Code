# Gwyddion Processing Pipeline

This repository provides a lightweight Python implementation of several
Gwyddion-inspired operations without external numerical dependencies.

## Features

- Row alignment with optional scar removal before and after scaling.
- Generation of 2D PSDF images (written as portable graymap files) and angular
  spectra stored as CSV files.
- Frame stabilisation controlled by a single displacement percentage while
  always producing a frame-based video export.

## Running the Tests

```bash
python -m pytest
```
