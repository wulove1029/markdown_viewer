"""Report background work to a GUI-thread receiver without Qt teardown on workers.

A finished ``QRunnable`` is deleted on its worker thread, so everything the job
references is released there. When that includes a QObject (a signals object
the job created) or an object behind a bound-method connection (PySide follows
those through a weak reference and tears the connection down wherever the
object dies), Qt state is torn down on the worker while the GUI thread may be
destroying the receiver: a native crash.

* ``WorkerRelay`` keeps the signals object on the receiver's side. It is a
  child of the receiver living in the receiver's thread, so it is destroyed
  there. A job only gets ``relay.emit``, which crosses threads through a
  queued signal and becomes a no-op once the receiver is destroyed.
* ``CancelScope`` sets plain ``threading.Event`` cancel flags when its owner is
  destroyed. Cancel flags are never connected to a Qt signal themselves.
* ``call_on_destroyed`` hooks an owner's ``destroyed`` signal through a plain
  closure, which PySide holds strongly and releases on the owner's thread.
"""

from __future__ import annotations

import inspect
import threading
import weakref
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot


def call_on_destroyed(owner: QObject, callback: Callable[[], object]) -> Callable[..., None]:
    """Run *callback* when *owner* is destroyed and return the connected slot.

    The slot is a closure, so PySide keeps it strongly and drops it with the
    connection on the owner's thread. Connecting ``event.set`` or another
    bound method of a non-QObject directly would make PySide track that object
    through a weak reference instead. Keep the returned slot to disconnect it.
    """

    def _on_destroyed(*_args: object) -> None:
        callback()

    owner.destroyed.connect(_on_destroyed)
    return _on_destroyed


class CancelScope:
    """Cancel flags that are set when the scope closes or its owner dies.

    Flags are plain ``threading.Event`` objects that workers poll; only the
    GUI thread tracks, forgets or closes them. Flags that are already set are
    dropped the next time another flag is tracked.
    """

    def __init__(self, owner: QObject | None = None) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._flags: list[threading.Event] = []
        if owner is not None:
            call_on_destroyed(owner, self.close)

    @property
    def closed(self) -> bool:
        return self._closed

    def track(self, flag: threading.Event) -> threading.Event:
        """Set *flag* when the scope closes; a closed scope sets it at once."""
        with self._lock:
            closed = self._closed
            if not closed:
                self._flags = [tracked for tracked in self._flags if not tracked.is_set()]
                self._flags.append(flag)
        if closed:
            flag.set()
        return flag

    def forget(self, flag: threading.Event) -> None:
        """Stop tracking *flag*, for example after its job has reported."""
        with self._lock:
            self._flags = [tracked for tracked in self._flags if tracked is not flag]

    def close(self) -> None:
        """Set every tracked flag; later ``track`` calls set theirs at once."""
        with self._lock:
            self._closed = True
            flags, self._flags = self._flags, []
        for flag in flags:
            flag.set()


def _hold(callback: Callable[..., object]) -> Callable[[], Callable[..., object] | None]:
    # A bound method would keep its receiver alive through the relay, and a
    # job referencing the relay could then release the receiver on a worker.
    if inspect.ismethod(callback):
        return weakref.WeakMethod(callback)
    return lambda: callback


class _RelayBridge(QObject):
    delivered = Signal(object)

    def __init__(
        self,
        owner: QObject,
        callback: Callable[..., object],
        connection: Qt.ConnectionType,
    ) -> None:
        super().__init__()
        # Created on any thread, then handed to the owner's thread before it
        # becomes the owner's child (Qt refuses a parent in another thread).
        self.moveToThread(owner.thread())
        self.setParent(owner)
        self.closed = False
        self._callback = _hold(callback)
        self.delivered.connect(self._dispatch, connection)

    @Slot(object)
    def _dispatch(self, args: tuple) -> None:
        if self.closed:
            return
        callback = self._callback()
        if callback is not None:
            callback(*args)


class WorkerRelay(CancelScope):
    """Deliver ``emit(*args)`` from any thread as ``callback(*args)`` on the owner.

    Create the relay on the owner's thread and keep it for the owner's
    lifetime; hand jobs ``relay.emit`` (never the owner or a QObject). The
    signals object is a child of *owner*. When *owner* is destroyed the relay
    closes: tracked cancel flags are set, an emission already in progress
    finishes, and every later or still-queued report is dropped.

    A bound-method *callback* is held weakly. Any other callable is held
    strongly, so it must not capture the owner.

    ``emit`` on the thread the owner lives in calls back synchronously, as a
    signal with an automatic connection would; ``queued=True`` always defers
    to the owner's event loop. The relay may be created on another thread as
    long as nothing changes the owner's children at the same moment.
    """

    def __init__(
        self,
        owner: QObject,
        callback: Callable[..., object],
        *,
        queued: bool = False,
    ) -> None:
        connection = (
            Qt.ConnectionType.QueuedConnection if queued
            else Qt.ConnectionType.AutoConnection
        )
        self._bridge: _RelayBridge | None = _RelayBridge(owner, callback, connection)
        super().__init__(owner)

    def emit(self, *args: object) -> None:
        """Report *args* to the owner; thread-safe and a no-op once closed."""
        with self._lock:
            bridge = self._bridge
            if bridge is None:
                return
            if QThread.currentThread() is not bridge.thread():
                # Held across the emission so close() cannot return, and the
                # owner cannot go on to delete the bridge, while another
                # thread is emitting.
                bridge.delivered.emit(args)
                return
        # On the bridge's own thread close() cannot run concurrently. The lock
        # is released first so a synchronous callback may close the relay or
        # wait for workers that are themselves trying to report.
        bridge.delivered.emit(args)

    def close(self) -> None:
        with self._lock:
            bridge, self._bridge = self._bridge, None
        if bridge is not None:
            bridge.closed = True
        super().close()
