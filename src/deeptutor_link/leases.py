"""Recovery for leases whose owner participated in our advisory lock protocol."""

import hashlib
from contextlib import contextmanager
from uuid import UUID, uuid4

from deeptutor_link.locking import LockBusy, advisory_lock


def mutation_lock_path(store, path_id):
    db = store.db_path.resolve()
    key = hashlib.sha256((str(db) + "\0" + path_id).encode()).hexdigest()
    return db.parent / f".deeptutor-codex-{key}.lock"


def owns_lease(lease, token):
    prefix = f"codex-lock-v1:{token}:"
    if lease.session_id == "__path_api__" and lease.turn_id.startswith(prefix):
        suffix = lease.turn_id[len(prefix) :]
        try:
            return str(UUID(suffix)) == suffix
        except ValueError:
            pass
    return False


def recover_locked_lease(store, path_id, token):
    lease = store.get_path_lease(path_id)
    if lease is None:
        return {"status": "clear", "recovered": False}
    if owns_lease(lease, token):
        removed = store.release_path_lease(path_id, turn_id=lease.turn_id)
        return {"status": "clear" if removed else "changed", "recovered": removed}
    # Old codex- leases lack proof of flock ownership. Never infer death from age.
    return {"status": "protected_lease", "recovered": False}


def recover_lease(store, path_id, *, inspect_only=False):
    from deeptutor_link.adapter import BridgeError

    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    try:
        with advisory_lock(mutation_lock_path(store, path_id)) as token:
            if inspect_only:
                lease = store.get_path_lease(path_id)
                if lease and owns_lease(lease, token):
                    return {"status": "orphan_recoverable", "recovered": False}
                return {"status": "protected_lease" if lease else "clear", "recovered": False}
            return recover_locked_lease(store, path_id, token)
    except LockBusy:
        return {"status": "extension_active", "recovered": False}


@contextmanager
def assessment_lease(store, path_id):
    from deeptutor.learning.storage import PathLeaseConflictError

    from deeptutor_link.adapter import BridgeError

    try:
        with advisory_lock(mutation_lock_path(store, path_id)) as token:
            recover_locked_lease(store, path_id, token)
            turn_id = f"codex-lock-v1:{token}:{uuid4()}"
            try:
                store.acquire_path_lease(path_id, "__path_api__", turn_id, bind_session=False)
            except PathLeaseConflictError as exc:
                raise BridgeError(
                    "path_busy", "A web/other learning turn is active. Retry after it ends."
                ) from exc
            try:
                yield turn_id
            finally:
                store.release_path_lease(path_id, turn_id=turn_id)
    except LockBusy as exc:
        raise BridgeError(
            "path_busy", "A AI assistant extension mutation is active. Retry later."
        ) from exc
