import os
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event

import pytest

from deeptutor_link.adapter import BridgeError, assess
from deeptutor_link.leases import mutation_lock_path, recover_lease
from deeptutor_link.locking import advisory_lock


@contextmanager
def paused_child(source, *args):
    from conftest import SOURCE

    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(SOURCE), str(Path(__file__).parents[1] / "src")]),
    }
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", source, *map(str, args)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert select.select([child.stdout], [], [], 15)[0], "child did not reach barrier"
        line = child.stdout.readline().strip()
        assert line == "READY", (
            line,
            child.poll(),
            child.stderr.read() if child.poll() is not None else "",
        )
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()


@pytest.mark.parametrize("committed", [False, True])
def test_sigkill_assessment_retry_recovers_real_sqlite(host_store, assessment, committed):
    source = """
import json, sys, threading
from pathlib import Path
from deeptutor.learning.storage import LearningStore
from deeptutor_link.adapter import assess
from deeptutor_link.models import Assessment
store = LearningStore(root=Path(sys.argv[1]))
def paused(*args, **kwargs):
    if sys.argv[3] == 'False':
        original(*args, **kwargs)
    print('READY', flush=True)
    threading.Event().wait()
if sys.argv[3] == 'True':
    store.release_path_lease = paused
else:
    original = store.acquire_path_lease
    store.acquire_path_lease = paused
assess(store, 'original-topic', Assessment.model_validate(json.loads(sys.argv[2])), '')
"""
    with paused_child(
        source, host_store.db_path.parent, assessment.model_dump_json(), committed
    ) as child:
        lease = host_store.get_path_lease("original-topic")
        assert lease.session_id == "__path_api__"
        with pytest.raises(BridgeError, match="active|busy"):
            assess(host_store, "original-topic", assessment, "")
        assert host_store.get_path_lease("original-topic") == lease
        child.kill()
        child.wait(timeout=10)
        result = assess(host_store, "original-topic", assessment, "")
    assert result["saved"] is True
    assert result["deduplicated"] is committed
    assert len(host_store.load("original-topic").learning_evidence) == 1
    assert host_store.get_path_lease("original-topic") is None


def test_threads_cannot_steal_active_mutation_or_split_inode(host_store, assessment):
    lock = mutation_lock_path(host_store, "original-topic")
    entered, finish = Event(), Event()

    def owner():
        with advisory_lock(lock):
            entered.set()
            assert finish.wait(10)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(owner)
        try:
            assert entered.wait(5)
            inode = lock.stat().st_ino
            assert recover_lease(host_store, "original-topic")["status"] == "extension_active"
            with pytest.raises(BridgeError, match="active"):
                assess(host_store, "original-topic", assessment, "")
            assert lock.stat().st_ino == inode
        finally:
            finish.set()
            future.result(timeout=10)
    with advisory_lock(lock):
        assert lock.stat().st_ino == inode
    assert lock.stat().st_ino == inode


@pytest.mark.parametrize(
    "session,turn",
    [
        ("__path_api__", "codex-legacy"),
        ("__path_api__", "admin-turn"),
        ("web-session", "web-turn"),
        ("__path_api__", "codex-lock-v1:foreign-token:fake-turn"),
    ],
)
def test_recovery_never_steals_unproven_or_native_leases(host_store, assessment, session, turn):
    lease = host_store.acquire_path_lease("original-topic", session, turn, bind_session=False)
    assert recover_lease(host_store, "original-topic")["status"] == "protected_lease"
    with pytest.raises(BridgeError, match="active"):
        assess(host_store, "original-topic", assessment, "")
    assert host_store.get_path_lease("original-topic") == lease


def test_recovery_diagnosis_and_token_loss_fail_closed(host_store, assessment):
    from deeptutor_link.adapter import context

    lock = mutation_lock_path(host_store, "original-topic")
    with advisory_lock(lock) as token:
        host_store.acquire_path_lease(
            "original-topic",
            "__path_api__",
            f"codex-lock-v1:{token}:{assessment.assessment_id}",
            bind_session=False,
        )
    before = host_store.load("original-topic").model_dump()
    assert (
        context(host_store, "original-topic", "")["mutation_lease"]["status"]
        == "orphan_recoverable"
    )
    assert host_store.get_path_lease("original-topic") is not None
    assert recover_lease(host_store, "original-topic") == {"status": "clear", "recovered": True}
    assert host_store.load("original-topic").model_dump() == before
    with advisory_lock(lock) as token:
        host_store.acquire_path_lease(
            "original-topic",
            "__path_api__",
            f"codex-lock-v1:{token}:{assessment.assessment_id}",
            bind_session=False,
        )
    # Simulate externally lost coordination metadata, after all holders exited.
    # Production never unlinks a lock. A replacement token must NOT reclaim it.
    lock.unlink()
    assert recover_lease(host_store, "original-topic")["status"] == "protected_lease"
