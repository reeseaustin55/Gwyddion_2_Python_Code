"""Utilities for importing the Gwyddion Python bindings."""

from __future__ import absolute_import

import logging
import os
import sys


DEFAULT_SEARCH_PATHS = [
    r"C:\\Program Files\\Gwyddion\\bin",
    r"C:\\Program Files (x86)\\Gwyddion\\bin",
]


def import_gwyddion(additional_paths=None, logger=None):
    """Import and return the :mod:`gwy` module.

    Parameters
    ----------
    additional_paths : Iterable[str], optional
        Extra paths to prepend when searching for the module.
    logger : :class:`logging.Logger`, optional
        Logger used for diagnostics.

    Raises
    ------
    ImportError
        If the module cannot be imported.
    """
    logger = logger or logging.getLogger(__name__)

    paths = list(additional_paths or []) + DEFAULT_SEARCH_PATHS
    for path in paths:
        if path and os.path.isdir(path) and path not in sys.path:
            logger.debug('Adding %s to Python path for Gwyddion import', path)
            sys.path.append(path)

    try:
        # Import locally to avoid leaking the name if import fails.
        gwy = __import__('gwy')
    except ImportError:
        formatted_paths = '\n'.join(paths)
        raise ImportError(
            'Unable to import the Gwyddion Python bindings (gwy).\n'
            'Searched the following locations:\n%s' % formatted_paths
        )

    return gwy
