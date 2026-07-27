from __future__ import annotations

import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path

from mail_agent.lock import ProcessLock, inspect_process_lock


def _hold_lock(path: str, ready: multiprocessing.Event, release: multiprocessing.Event) -> None:
    with ProcessLock(Path(path)):
        ready.set()
        release.wait(10)


class MailLockStatusTests(unittest.TestCase):
    def test_free_lock_is_reported_without_deleting_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mail.lock"
            path.write_text("999999\n", encoding="utf-8")
            result = inspect_process_lock(path)
            self.assertTrue(result["ok"])
            self.assertFalse(result["locked"])
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "999999\n")

    def test_held_lock_reports_owner_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mail.lock"
            ready = multiprocessing.Event()
            release = multiprocessing.Event()
            process = multiprocessing.Process(target=_hold_lock, args=(str(path), ready, release))
            process.start()
            try:
                self.assertTrue(ready.wait(5))
                result = inspect_process_lock(path)
                self.assertTrue(result["ok"])
                self.assertTrue(result["locked"])
                self.assertEqual(result["pid"], process.pid)
                self.assertTrue(result["process_alive"])
            finally:
                release.set()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(2)


if __name__ == "__main__":
    unittest.main()
