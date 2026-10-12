"""Bounded worker pool. End submits a task; a worker runs the handler End registered."""

import os
import queue
import threading
import time
import traceback


def _worker_count():
    raw = (os.environ.get("AIVIDEO_AGENT_WORKERS") or "").strip()
    if not raw:
        return 4
    try:
        count = int(raw)
    except ValueError:
        return 4
    return count if count > 0 else 4


class Task(object):
    def __init__(self, kind, project_id, payload=None):
        self.kind = str(kind or "")
        self.project_id = str(project_id or "")
        self.payload = dict(payload or {})


class TaskQueue(object):
    def __init__(self, handler, workers=None):
        if workers is None:
            workers = _worker_count()
        self.workers = max(1, int(workers))
        self._handler = handler
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = set()
        self._threads = []
        self._stop = threading.Event()

    def start(self):
        with self._lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            if self._threads:
                return
            self._stop.clear()
            for index in range(self.workers):
                thread = threading.Thread(
                    target=self._loop,
                    name="aivideo-agent-%s" % (index + 1),
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def submit(self, task):
        key = (task.kind, task.project_id)
        with self._lock:
            if key in self._pending:
                return False
            self._pending.add(key)
        self.start()
        self._queue.put(task)
        return True

    def wait_until_idle(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._pending:
                    return True
            time.sleep(0.01)
        return False

    def stop(self):
        self._stop.set()
        threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=2)
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
        with self._lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            if not self._threads:
                self._pending.clear()
        self._stop.clear()

    def _loop(self):
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handler(task)
            except Exception:
                traceback.print_exc()
            finally:
                with self._lock:
                    self._pending.discard((task.kind, task.project_id))
                self._queue.task_done()
