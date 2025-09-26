#!/usr/bin/env python
"""Launch the Tkinter GUI for the Gwyddion batch processor."""

from __future__ import absolute_import, print_function

from gwyddion_batch.gui import BatchProcessorGUI


def main():  # pragma: no cover - simple wrapper
    gui = BatchProcessorGUI()
    gui.run()


if __name__ == '__main__':
    main()
