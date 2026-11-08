"""Reentrant cross-process serialization for CPU writes and restoration."""

from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import threading
import time

from senos.recovery.errors import RestoreError

_MUTEX = threading.RLock()
_LOCAL = threading.local()


@contextmanager
def cpu_lock(database: Path):
    key = str(database.resolve())
    with _MUTEX:
        held = getattr(_LOCAL, "held", {})
        _LOCAL.held = held
        if key in held:
            yield
            return
        descriptor = None
        try:
            path = Path(key + ".cpu.lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            deadline = time.monotonic() + 10
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        if os.fstat(descriptor).st_size == 0:
                            os.write(descriptor, b"0")
                        os.lseek(descriptor, 0, 0)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RestoreError("Another CPU operation is busy; retry")
                    time.sleep(0.05)
            held[key] = descriptor
            yield
        except OSError as error:
            raise RestoreError("Cannot serialize CPU operations; check database permissions") from error
        finally:
            held.pop(key, None)
            if descriptor is not None:
                os.close(descriptor)


def serialized(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        database = self.database if hasattr(self, "database") else self.store.path
        if not database.exists():
            return function(self, *args, **kwargs)
        with cpu_lock(database):
            return function(self, *args, **kwargs)
    return wrapped
