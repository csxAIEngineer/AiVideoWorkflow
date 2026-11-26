"""End TaskQueue exposes waiting vs running without changing the pool cap."""

import threading
import time
import unittest

from end.task_queue import Task, TaskQueue


class TaskQueueWaitStateTest(unittest.TestCase):
    def test_submitted_tasks_are_waiting_until_a_worker_starts(self):
        started = threading.Event()
        release = threading.Event()

        def handler(task):
            del task
            started.set()
            release.wait(2)

        queue = TaskQueue(handler, workers=1)
        try:
            self.assertTrue(queue.submit(Task("write", "p1")))
            self.assertTrue(queue.submit(Task("write", "p2")))
            self.assertFalse(queue.submit(Task("write", "p1")))
            self.assertTrue(started.wait(2))
            # Give the worker a moment to leave the waiting list.
            deadline = time.time() + 1
            while time.time() < deadline:
                if queue.state_for("p1").get("worker_state") == "running":
                    break
                time.sleep(0.01)
            first = queue.state_for("p1")
            second = queue.state_for("p2")
            idle = queue.state_for("missing")
            self.assertEqual(first["worker_state"], "running")
            self.assertEqual(first["worker_kind"], "write")
            self.assertNotIn("worker_wait_position", first)
            self.assertEqual(second["worker_state"], "waiting")
            self.assertEqual(second["worker_kind"], "write")
            self.assertEqual(second["worker_wait_position"], 1)
            self.assertEqual(idle["worker_state"], "")
        finally:
            release.set()
            queue.stop()

    def test_several_waiting_projects_keep_their_order(self):
        release = threading.Event()
        entered = []
        lock = threading.Lock()

        def handler(task):
            with lock:
                entered.append(task.project_id)
            release.wait(2)

        queue = TaskQueue(handler, workers=1)
        try:
            for index in range(1, 5):
                self.assertTrue(queue.submit(Task("write", "p%s" % index)))
            deadline = time.time() + 2
            while time.time() < deadline:
                with lock:
                    if entered:
                        break
                time.sleep(0.01)
            self.assertEqual(queue.state_for("p1")["worker_state"], "running")
            self.assertEqual(queue.state_for("p2")["worker_wait_position"], 1)
            self.assertEqual(queue.state_for("p3")["worker_wait_position"], 2)
            self.assertEqual(queue.state_for("p4")["worker_wait_position"], 3)
            self.assertEqual(queue.state_for("p2")["worker_state"], "waiting")
            self.assertEqual(queue.state_for("p4")["worker_state"], "waiting")
        finally:
            release.set()
            queue.stop()


if __name__ == "__main__":
    unittest.main()
