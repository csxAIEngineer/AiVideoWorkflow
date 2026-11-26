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
        self._waiting = []
        self._running = set()
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
            self._waiting.append(task)
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
        drained = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
            self._queue.task_done()
        with self._lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            # Discarded tasks must release their dedup keys and waiting slots
            # even when a handler outran the join timeout; otherwise the same
            # (kind, project) could never be submitted again.
            for task in drained:
                self._pending.discard((task.kind, task.project_id))
                self._waiting = [item for item in self._waiting if item is not task]
            if not self._threads:
                self._pending.clear()
                self._waiting = []
                self._running.clear()
        self._stop.clear()

    def _loop(self):
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            key = (task.kind, task.project_id)
            with self._lock:
                self._waiting = [item for item in self._waiting if item is not task]
                self._running.add(key)
            try:
                self._handler(task)
            except Exception:
                traceback.print_exc()
            finally:
                with self._lock:
                    self._running.discard(key)
                    self._pending.discard(key)
                self._queue.task_done()

    def state_for(self, project_id):
        """Live queue state for one project.

        ``waiting`` means the task is submitted and no worker has started
        the handler yet. ``running`` means a worker is in the handler.
        An empty ``worker_state`` means this project is not in the queue.
        ``worker_wait_position`` is 1-based among tasks still waiting, and
        is only set when the state is waiting.
        """
        project_id = str(project_id or "")
        with self._lock:
            running = [kind for kind, item_id in self._running if item_id == project_id]
            if running:
                return {"worker_state": "running", "worker_kind": running[0]}
            for index, task in enumerate(self._waiting):
                if task.project_id == project_id:
                    return {
                        "worker_state": "waiting",
                        "worker_kind": task.kind,
                        "worker_wait_position": index + 1,
                    }
        return {"worker_state": "", "worker_kind": ""}

    def snapshot(self):
        """All waiting and running tasks, for list refresh. No payloads."""
        rows = []
        with self._lock:
            for kind, project_id in self._running:
                rows.append({
                    "id": project_id,
                    "worker_state": "running",
                    "worker_kind": kind,
                })
            for index, task in enumerate(self._waiting):
                rows.append({
                    "id": task.project_id,
                    "worker_state": "waiting",
                    "worker_kind": task.kind,
                    "worker_wait_position": index + 1,
                })
        return rows
