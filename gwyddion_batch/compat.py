"""Helpers for writing Python 2 and Python 3 compatible code."""

from __future__ import absolute_import

import sys


if sys.version_info[0] >= 3:
    text_type = str
    binary_type = bytes

    def to_native_path(value):
        """Return ``value`` unchanged on Python 3."""
        return value

else:  # pragma: no cover - exercised when running on Python 2.7
    text_type = unicode  # type: ignore[name-defined]
    binary_type = str

    def to_native_path(value):
        """Encode ``value`` to the filesystem encoding for Python 2 callers."""
        if isinstance(value, text_type):
            encoding = sys.getfilesystemencoding() or 'utf-8'
            return value.encode(encoding)
        return value


__all__ = ['text_type', 'binary_type', 'to_native_path']
