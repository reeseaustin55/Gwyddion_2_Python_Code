"""User interface helpers for configuring Gwyddion sfunctions processing.

This module provides GTK+ widgets and processing helpers for generating
power spectral density (PSD) related outputs from Gwyddion.  It extends the
options dialog with check boxes for 2D PSDF generation and Angular Spectrum
computation, including handling of the zoom factor required by the 2D PSDF
mode in Gwyddion's statistics tab.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, List

import importlib.util

if importlib.util.find_spec("gwy") is None:  # pragma: no cover - environment guard
    raise ImportError(
        "The 'gwy' module is required to use the sfunctions UI helpers."
    )

import gwy
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SFunctionsOptions:
    """Selection state for the sfunctions operations."""

    create_psdf_image: bool = False
    create_psdf2d: bool = False
    psdf2d_zoom: int = 8
    create_angular_spectrum: bool = False

    def clone(self) -> "SFunctionsOptions":
        """Return a copy of the options."""

        return SFunctionsOptions(
            create_psdf_image=self.create_psdf_image,
            create_psdf2d=self.create_psdf2d,
            psdf2d_zoom=self.psdf2d_zoom,
            create_angular_spectrum=self.create_angular_spectrum,
        )


# ---------------------------------------------------------------------------
# GTK UI
# ---------------------------------------------------------------------------

class SFunctionsOptionsPane(Gtk.Box):
    """Widget that exposes check boxes for the supported outputs."""

    def __init__(self, options: SFunctionsOptions | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_border_width(12)

        self._options = options.clone() if options else SFunctionsOptions()

        self._create_psdf_image = Gtk.CheckButton(label="Create PSDF image")
        self._create_psdf_image.set_active(self._options.create_psdf_image)
        self.pack_start(self._create_psdf_image, False, False, 0)

        psdf2d_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._create_psdf2d = Gtk.CheckButton(label="Generate 2D PSDF")
        self._create_psdf2d.set_active(self._options.create_psdf2d)
        psdf2d_box.pack_start(self._create_psdf2d, False, False, 0)

        zoom_adjustment = Gtk.Adjustment(
            value=float(self._options.psdf2d_zoom),
            lower=1.0,
            upper=256.0,
            step_increment=1.0,
            page_increment=2.0,
        )
        self._psdf2d_zoom = Gtk.SpinButton(adjustment=zoom_adjustment, digits=0)
        self._psdf2d_zoom.set_value(self._options.psdf2d_zoom)
        self._psdf2d_zoom.set_width_chars(4)
        psdf2d_box.pack_start(Gtk.Label(label="Zoom"), False, False, 0)
        psdf2d_box.pack_start(self._psdf2d_zoom, False, False, 0)
        psdf2d_box.pack_start(Gtk.Label(label="×"), False, False, 0)
        self.pack_start(psdf2d_box, False, False, 0)

        self._create_angular_spectrum = Gtk.CheckButton(
            label="Generate Angular Spectrum"
        )
        self._create_angular_spectrum.set_active(
            self._options.create_angular_spectrum
        )
        self.pack_start(self._create_angular_spectrum, False, False, 0)

        self._create_psdf2d.connect("toggled", self._on_psdf2d_toggled)
        self._on_psdf2d_toggled(self._create_psdf2d)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_psdf2d_toggled(self, button: Gtk.CheckButton) -> None:
        self._psdf2d_zoom.set_sensitive(button.get_active())

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def update_options(self) -> SFunctionsOptions:
        """Synchronise widget state back to an options object."""

        self._options.create_psdf_image = self._create_psdf_image.get_active()
        self._options.create_psdf2d = self._create_psdf2d.get_active()
        self._options.psdf2d_zoom = int(self._psdf2d_zoom.get_value())
        self._options.create_angular_spectrum = (
            self._create_angular_spectrum.get_active()
        )
        return self._options.clone()


# ---------------------------------------------------------------------------
# Processing helpers
# ---------------------------------------------------------------------------

def _enum_value(default: int, *names: str) -> int:
    for name in names:
        if hasattr(gwy, name):
            return getattr(gwy, name)
    return default


SF_METHOD_PSD2D = _enum_value(3, "SF_METHOD_PSD2D", "SF_METHOD_PSDF2D")
SF_OUTPUT_PSDF2D = _enum_value(13, "SF_OUTPUT_PSDF2D", "SF_OUTPUT_PSDF_IMAGE")
SF_OUTPUT_ANGULAR_SPECTRUM = _enum_value(
    14,
    "SF_OUTPUT_ANGULAR_SPECTRUM",
    "SF_OUTPUT_SPECTRUM_ANGULAR",
)
WINDOW_BLACKMAN = _enum_value(3, "SF_WINDOW_BLACKMAN", "SF_WINDOW_BLACKMAN_HARRIS")
MASK_IGNORE = _enum_value(0, "MASK_IGNORE")


@contextmanager
def _sfunctions_settings() -> Iterator[gwy.GwySettings]:
    settings = gwy.gwy_app_settings_get()
    settings.begin_group("/module/sfunctions")
    try:
        yield settings
    finally:
        settings.end_group()


def run_psdf_image(container: gwy.GwyContainer) -> None:
    """Run the sfunctions module to create a standard PSDF image."""

    with _sfunctions_settings() as settings:
        settings["method"] = SF_METHOD_PSD2D
        settings["output_type"] = SF_OUTPUT_PSDF2D
    gwy.gwy_process_func_run("sfunctions", container, gwy.RUN_IMMEDIATE)


def run_psdf2d(container: gwy.GwyContainer, zoom_factor: int) -> None:
    """Generate the 2D PSDF with the requested zoom factor."""

    with _sfunctions_settings() as settings:
        settings["method"] = SF_METHOD_PSD2D
        settings["output_type"] = SF_OUTPUT_PSDF2D
        settings["psdf_zoom"] = zoom_factor
    gwy.gwy_process_func_run("sfunctions", container, gwy.RUN_IMMEDIATE)


def run_angular_spectrum(container: gwy.GwyContainer) -> gwy.GwyDataLine | None:
    """Compute the angular spectrum for the container.

    The Angular Spectrum is produced using the 2D PSDF method with
    the output type set appropriately and default processing parameters
    derived from the Gwyddion documentation.
    """

    with _sfunctions_settings() as settings:
        settings["method"] = SF_METHOD_PSD2D
        settings["output_type"] = SF_OUTPUT_ANGULAR_SPECTRUM
        settings["resolution"] = 120
        settings["windowing"] = WINDOW_BLACKMAN
        settings["masking"] = MASK_IGNORE

    gwy.gwy_process_func_run("sfunctions", container, gwy.RUN_IMMEDIATE)
    return container.get_object_by_name("/0/graph/0/data")


def process_containers(containers: Iterable[gwy.GwyContainer], options: SFunctionsOptions) -> List[gwy.GwyDataLine]:
    """Apply the selected processing options to each container.

    Returns a list of :class:`gwy.GwyDataLine` instances generated by the
    Angular Spectrum processing (if requested).
    """

    options = options.clone()
    angular_spectra: List[gwy.GwyDataLine] = []

    for index, container in enumerate(containers):
        if options.create_psdf_image:
            run_psdf_image(container)

        if options.create_psdf2d:
            run_psdf2d(container, options.psdf2d_zoom)

        if options.create_angular_spectrum:
            spectrum = run_angular_spectrum(container)
            if spectrum is not None:
                angular_spectra.append(spectrum)

    return angular_spectra


__all__ = [
    "SFunctionsOptions",
    "SFunctionsOptionsPane",
    "process_containers",
    "run_psdf_image",
    "run_psdf2d",
    "run_angular_spectrum",
]
