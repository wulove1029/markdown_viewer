"""Opt-in diagnostic: reject restoration of methods on deleted Qt objects."""

from PySide6.QtCore import QObject
from pytest import MonkeyPatch
from shiboken6 import isValid

original_undo = MonkeyPatch.undo


def checked_undo(self):
    invalid = []
    safe = []
    for obj, name, _value in self._setattr:
        if isinstance(obj, QObject) and not isValid(obj):
            invalid.append(name)
        else:
            safe.append((obj, name, _value))
    # Finish other restoration so a diagnostic failure cannot leak fake
    # settings or global patches into subsequent tests. Invalid native objects
    # cannot safely be restored; always report them as a teardown failure.
    self._setattr[:] = safe
    original_undo(self)
    if invalid:
        raise AssertionError(f'monkeypatch restore targets deleted QObject: {invalid}')


MonkeyPatch.undo = checked_undo
