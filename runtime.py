"""Single-instance advisory lock and process-tree cleanup."""

import os
import signal
import subprocess
from pathlib import Path

from errors import AppError


class DatabaseLock:
    def __init__(self, database):
        self.path = Path(str(Path(database).resolve()) + ".lock")
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0, 2)
                if self.file.tell() == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise AppError(
                "SESSION_BUSY", "该数据库已有应用或维护进程运行，请先停止它。", 409
            ) from None
        return self

    def close(self):
        if self.file:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()


def kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True
        )
    else:
        try:
            rows = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True)
            children = {}
            for line in rows.splitlines():
                pid, parent = map(int, line.split())
                children.setdefault(parent, []).append(pid)

            def descendants(pid):
                result = []
                for child in children.get(pid, []):
                    result.extend(descendants(child))
                    result.append(child)
                return result

            for pid in descendants(process.pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                os.kill(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
