"""Process-released local POSIX locks. Lock files must never be unlinked."""

import os
import stat
import sys
from contextlib import contextmanager
from uuid import UUID, uuid4


class LockBusy(ValueError):
    pass


def require_lock_support():
    if sys.platform not in {"darwin", "linux"}:
        raise ValueError("The server extension requires macOS/Linux and local POSIX flock.")


@contextmanager
def advisory_lock(path):
    require_lock_support()
    import fcntl

    # O_NOFOLLOW and a stable inode prevent aliases and split locks. A fresh open
    # for each caller also excludes concurrent threads in the same process.
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Lock must be a regular file with one link.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy("Another operation holds this lock.") from exc
        token = os.read(fd, 100)
        if not token:
            token = str(uuid4()).encode("ascii")
            if os.write(fd, token) != len(token):
                raise ValueError("Incomplete lock identity; manual inspection required.")
            os.fsync(fd)
        if len(token) != 36 or str(UUID(token.decode("ascii"))).encode() != token:
            raise ValueError("Invalid lock identity; manual inspection required.")
        linked = os.stat(path, follow_symlinks=False)
        if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Lock file changed while acquiring it.")
        yield token.decode("ascii")
    finally:
        # Closing releases flock, including on SIGKILL. Keep the inode and token.
        os.close(fd)
