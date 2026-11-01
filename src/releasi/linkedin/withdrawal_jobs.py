"""Private durable withdrawal progress; no automatic replay after failure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID, uuid4

from releasi.linkedin.withdrawal_safety import MAX_WITHDRAWALS, WithdrawalStopped


class WithdrawalJobStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.active = {}  # account -> job, reserved before background work queues

    def _path(self, account_id):
        UUID(account_id)
        return self.directory / (account_id + ".json")

    def _read(self, account_id):
        try:
            return json.loads(self._path(account_id).read_text())
        except FileNotFoundError:
            return {}

    def update(self, account_key, **fields):
        state = self._read(account_key)
        state.update(fields, updated_at=time.time())
        self._write(self._path(account_key), state)
        return state

    def _write(self, path, state):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(state, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def read_job(self, account_id, job_id):
        UUID(job_id.removeprefix("wd-"))
        current = self.read(account_id)
        if current.get("job_id") == job_id:
            return current
        try:
            state = json.loads((self.directory / (job_id + ".json")).read_text())
        except FileNotFoundError:
            return {}
        return state if state.get("account_id") == account_id else {}

    def read(self, account_id):
        state = self._read(account_id)
        if state.get("status") == "running" and self.active.get(account_id) != state.get("job_id"):
            state = self.update(
                account_id, status="error", needs_review=bool(state.get("needs_review") or state.get("in_flight")),
                error="Withdrawal interrupted. Saved progress is retained; no automatic retry will run.",
            )
        return state

    def claim(self, account_id, count, order, *, automatic=False):
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_WITHDRAWALS:
            raise WithdrawalStopped("Withdrawal count must be between 1 and 100.")
        if order not in ("oldest", "newest"):
            raise WithdrawalStopped("Withdrawal order must be oldest or newest.")
        previous = self.read(account_id)
        if account_id in self.active:
            raise WithdrawalStopped("A withdrawal is already queued or running for this account.")
        if previous.get("needs_review"):
            raise WithdrawalStopped("A previous withdrawal has an uncertain result; operator review is required before retrying.")
        if automatic and previous.get("status") == "error":
            raise WithdrawalStopped("Automatic withdrawals are held after a failed run; manual review is required.")
        job_id = "wd-" + uuid4().hex
        self.active[account_id] = job_id
        try:
            if previous.get("job_id"):
                UUID(previous["job_id"].removeprefix("wd-"))
                self._write(self.directory / (previous["job_id"] + ".json"), previous)
            self.update(account_id, job_id=job_id, account_id=account_id, count=count,
                        order=order, status="running", started_at=time.time(),
                        withdrawn=[], db_updated=0, in_flight=None,
                        needs_review=False, error=None, automatic=automatic)
        except BaseException:
            self.release(account_id)
            raise
        return job_id

    def release(self, account_id, job_id=None):
        if job_id is None or self.active.get(account_id) == job_id:
            self.active.pop(account_id, None)


_store = None


def get_withdrawal_job_store():
    global _store
    if _store is None:
        from releasi.config import get_settings
        db_path = Path(get_settings().db_url.split("///", 1)[-1])
        _store = WithdrawalJobStore(db_path.parent / "withdrawal_jobs")
    return _store
