"""Worker reports reach the GUI thread; no Qt teardown is left to the worker."""

import gc
import random
import threading
import time
import weakref

from PySide6.QtCore import (
    QCoreApplication,
    QElapsedTimer,
    QEvent,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
)
from PySide6.QtTest import QTest
from shiboken6 import delete, isValid

from app.worker_relay import CancelScope, WorkerRelay, call_on_destroyed


class _Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []

    def report(self, *args):
        self.calls.append((threading.get_ident(), args))


class _Job(QRunnable):
    def __init__(self, emit, *args, before=None):
        super().__init__()
        self.emit, self.args, self.before = emit, args, before

    def run(self):
        if self.before is not None:
            self.before()
        self.emit(*self.args)


def _wait(qapp, predicate, timeout_ms=5000):
    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < timeout_ms:
        qapp.processEvents()
        QTest.qWait(5)
    return predicate()


def test_report_from_worker_thread_runs_on_owner_thread(qapp):
    receiver = _Receiver()
    relay = WorkerRelay(receiver, receiver.report)
    payload = object()
    worker_ids = []

    def emit():
        worker_ids.append(threading.get_ident())
        relay.emit(7, "text", payload)

    thread = threading.Thread(target=emit)
    thread.start()
    thread.join(5)
    assert receiver.calls == []  # queued to the owner's event loop
    assert _wait(qapp, lambda: receiver.calls)
    assert receiver.calls == [(threading.get_ident(), (7, "text", payload))]
    assert worker_ids and worker_ids[0] != threading.get_ident()
    delete(receiver)


def test_report_from_thread_pool_job_arrives_with_original_objects(qapp):
    receiver = _Receiver()
    relay = WorkerRelay(receiver, receiver.report)
    headings = [("Title", 1)]
    QThreadPool.globalInstance().start(_Job(relay.emit, 3, None, headings))
    assert QThreadPool.globalInstance().waitForDone(5000)
    assert _wait(qapp, lambda: receiver.calls)
    assert receiver.calls[0][1][2] is headings
    delete(receiver)


def test_owner_thread_report_is_direct_unless_queued(qapp):
    receiver = _Receiver()
    direct = WorkerRelay(receiver, receiver.report)
    queued = WorkerRelay(receiver, lambda *args: receiver.calls.append(("queued", args)),
                         queued=True)
    direct.emit(1)
    queued.emit(2)
    assert receiver.calls == [(threading.get_ident(), (1,))]
    assert _wait(qapp, lambda: len(receiver.calls) == 2)
    assert receiver.calls[1] == ("queued", (2,))
    delete(receiver)


def test_reports_after_owner_destroyed_are_silent_no_ops(qapp):
    receiver = _Receiver()
    calls = receiver.calls
    relay = WorkerRelay(receiver, receiver.report)
    bridge = relay._bridge
    destroyed_on = []
    bridge.destroyed.connect(lambda *_: destroyed_on.append(threading.get_ident()),
                             Qt.ConnectionType.DirectConnection)
    delete(receiver)
    assert relay.closed
    assert destroyed_on == [threading.get_ident()]
    errors = []

    def emit():
        try:
            relay.emit("late")
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    thread = threading.Thread(target=emit)
    thread.start()
    thread.join(5)
    relay.emit("late on owner thread")
    qapp.processEvents()
    assert errors == [] and calls == []


def test_report_queued_before_owner_destroyed_is_dropped(qapp):
    receiver = _Receiver()
    delivered = []
    relay = WorkerRelay(receiver, lambda *args: delivered.append(args), queued=True)
    relay.emit("pending")
    delete(receiver)
    qapp.processEvents()
    assert delivered == []


def test_explicit_close_drops_pending_reports_and_keeps_owner_usable(qapp):
    receiver = _Receiver()
    relay = WorkerRelay(receiver, receiver.report)
    thread = threading.Thread(target=relay.emit, args=("pending",))
    thread.start()
    thread.join(5)
    relay.close()
    relay.close()  # idempotent
    qapp.processEvents()
    relay.emit("after close")
    assert receiver.calls == []
    assert isValid(receiver)
    delete(receiver)


def test_relay_holds_bound_method_callback_weakly(qapp):
    receiver = _Receiver()
    relay = WorkerRelay(receiver, receiver.report)
    ref = weakref.ref(receiver)
    del receiver
    gc.collect()
    assert ref() is None  # nothing in the relay kept the receiver alive
    assert relay.closed
    relay.emit("after receiver collected")


def test_cancel_flags_are_set_when_owner_is_destroyed(qapp):
    receiver = _Receiver()
    relay = WorkerRelay(receiver, receiver.report)
    running = relay.track(threading.Event())
    finished = relay.track(threading.Event())
    relay.forget(finished)
    assert not running.is_set()
    # Tracking never hands the flag to Qt: no weak reference, no connection.
    assert weakref.getweakrefcount(running) == 0
    delete(receiver)
    assert running.is_set()
    assert not finished.is_set()
    late = relay.track(threading.Event())
    assert late.is_set()


def test_cancel_scope_drops_flags_that_are_already_set(qapp):
    owner = QObject()
    scope = CancelScope(owner)
    old = scope.track(threading.Event())
    old.set()
    new = scope.track(threading.Event())
    assert scope._flags == [new]
    delete(owner)
    assert scope.closed and new.is_set()


def test_call_on_destroyed_uses_a_strong_closure_that_can_be_disconnected(qapp):
    owner = QObject()
    event = threading.Event()
    slot = call_on_destroyed(owner, event.set)
    assert weakref.getweakrefcount(event) == 0
    owner.destroyed.disconnect(slot)
    delete(owner)
    assert not event.is_set()
    owner = QObject()
    call_on_destroyed(owner, event.set)
    delete(owner)
    assert event.is_set()


def test_finished_jobs_release_no_qt_object_on_worker_threads(qapp):
    receiver = _Receiver()
    relay = WorkerRelay(receiver, receiver.report)
    bridge = relay._bridge
    destroyed_on = []
    bridge.destroyed.connect(lambda *_: destroyed_on.append(threading.get_ident()),
                             Qt.ConnectionType.DirectConnection)
    for index in range(20):
        QThreadPool.globalInstance().start(_Job(relay.emit, index))
    assert QThreadPool.globalInstance().waitForDone(5000)
    gc.collect()
    assert _wait(qapp, lambda: len(receiver.calls) == 20)
    assert destroyed_on == [] and isValid(bridge)
    delete(receiver)
    assert destroyed_on == [threading.get_ident()]


def test_destroying_owners_while_workers_report_never_crashes(qapp):
    pool = QThreadPool.globalInstance()
    rng = random.Random(1234)
    delivered = []
    for _ in range(150):
        receiver = _Receiver()
        relay = WorkerRelay(receiver, receiver.report)
        cancel = relay.track(threading.Event())
        for index in range(3):
            delay = rng.random() * 0.003
            pool.start(_Job(relay.emit, index, before=lambda d=delay: time.sleep(d)))
        time.sleep(rng.random() * 0.003)
        qapp.processEvents()
        delivered.append(len(receiver.calls))
        if rng.random() < 0.5:
            delete(receiver)
        else:
            receiver.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        del receiver
        assert cancel.is_set()
    assert pool.waitForDone(10000)
    qapp.processEvents()
    gc.collect()
    assert all(count <= 3 for count in delivered)


def test_relay_created_on_another_thread_reports_on_the_owner_thread(qapp):
    # The same-thread shortcut follows the thread the bridge lives in (the
    # owner's), not the thread that happened to create the relay.
    receiver = _Receiver()
    created, errors = [], []

    def create():
        try:
            created.append(WorkerRelay(receiver, receiver.report))
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    creator = threading.Thread(target=create)
    creator.start()
    creator.join(5)
    assert errors == []
    (relay,) = created
    main = threading.get_ident()
    assert relay._bridge.thread() is receiver.thread()
    assert relay._bridge.parent() is receiver
    relay.emit("from owner thread")
    assert receiver.calls == [(main, ("from owner thread",))]  # synchronous, as before
    worker = threading.Thread(target=relay.emit, args=("from worker",))
    worker.start()
    worker.join(5)
    assert len(receiver.calls) == 1  # queued until the owner's loop runs
    assert _wait(qapp, lambda: len(receiver.calls) == 2)
    assert receiver.calls[1] == (main, ("from worker",))
    cancel = relay.track(threading.Event())
    delete(receiver)
    assert relay.closed and cancel.is_set()
    relay.emit("after close")
