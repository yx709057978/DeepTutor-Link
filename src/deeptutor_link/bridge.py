import asyncio
import hashlib
import json
import time
from typing import Any

from deeptutor_link.adapter import BridgeError
from deeptutor_link.leases import (
    assessment_lease,
    mutation_lock_path,
    recover_locked_lease,
)
from deeptutor_link.locking import LockBusy, advisory_lock


def _private_projection(value: Any, depth: int = 0):
    if depth > 12:
        return None
    if isinstance(value, list):
        return [_private_projection(item, depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        blocked = {"answer", "learner_answer", "user_answer", "expected_answer", "correct_answer", "answer_key", "explanation"}
        return {
            key: _private_projection(item, depth + 1)
            for key, item in list(value.items())[:200]
            if key.casefold() not in blocked
        }
    if isinstance(value, str):
        return value[:24000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def objective_state(store, path_id: str, objective_id: str) -> dict:
    from deeptutor.learning import policy

    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    kp, module_id, module_name = policy.find_knowledge_point(progress, objective_id)
    if kp is None:
        raise BridgeError("unknown_objective", "Objective not found on this topic.", 404)
    state = progress.repetition_states.get(kp.id)
    due = next((item.due_at for item in progress.review_queue if item.knowledge_point_id == kp.id), None)
    errors = [
        {
            "id": row.id,
            "error_type": row.error_type.value,
            "status": row.status,
            "retries": len(row.retry_history),
            "created_at": row.created_at,
        }
        for row in progress.error_records
        if row.knowledge_point_id == kp.id
    ][:20]
    return {
        "topic_id": path_id,
        "revision": progress.version,
        "objective": {
            "id": kp.id,
            "name": kp.name[:400],
            "type": kp.type.value,
            "module_id": module_id,
            "module_name": module_name[:400],
            "status": policy.objective_status(progress, kp),
            "mastered": policy.is_mastered(progress, kp),
            "assessed_mastered": policy.is_assessed_mastered(progress, kp),
            "mastery_source": policy.mastery_source(progress, kp),
            "mastery": policy.display_mastery(progress, kp),
            "threshold": policy.gate_threshold(kp.type),
            "correct_count": sum(
                1 for row in progress.quiz_attempts
                if row.knowledge_point_id == kp.id and row.is_correct
            ),
            "attempt_count": sum(1 for row in progress.quiz_attempts if row.knowledge_point_id == kp.id),
            "review": {
                "due_at": due,
                "review_count": state.review_count,
                "desired_retention": state.desired_retention,
                "lapse_count": state.lapse_count,
            } if state else None,
            "errors": errors,
        },
    }


def mastery_events(store, path_id: str, after_revision: int = 0) -> dict:
    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    events = store.list_events(path_id, after_revision=max(0, after_revision))
    return {
        "topic_id": path_id,
        "events": [
            {
                "id": row.id,
                "revision": row.revision,
                "event_type": row.event_type,
                "created_at": row.created_at,
                "payload": {
                    key: value
                    for key, value in _private_projection(row.payload).items()
                    if key in {"knowledge_point_id", "source", "result", "mastered", "desired_retention", "module_count", "source_count"}
                },
            }
            for row in events[:200]
        ],
    }


def review_settings(store, path_id: str) -> dict:
    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    return {
        "topic_id": path_id,
        "revision": progress.version,
        "desired_retention": progress.desired_retention,
    }


def mastery_tool_catalog() -> dict:
    from deeptutor.capabilities.mastery import tools as module
    from deeptutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES
    from deeptutor.core.tool_protocol import BaseTool

    available = {}
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and value is not BaseTool
            and issubclass(value, BaseTool)
            and value.__module__ == module.__name__
        ):
            tool = value()
            if tool.name in MASTERY_TOOL_NAMES:
                available[tool.name] = tool.get_definition().to_openai_schema()["function"]
    model_tools = {
        "mastery_grade": "semantic_answer_assessment_when_exact_matching_is_insufficient",
        "mastery_assess": "semantic_learner_explanation_assessment",
        "mastery_quiz": "host_model_may_bind_source_grounded_questions",
        "mastery_prepare_lesson": "source_preparation_may_use_host_models",
        "mastery_repair_question": "semantic_regrading_for_corrected_answer_keys",
    }
    return {
        "tools": [available[name] for name in MASTERY_TOOL_NAMES if name in available],
        "model_behavior": {
            name: model_tools.get(name, "no_model_call_by_tool_contract")
            for name in MASTERY_TOOL_NAMES
        },
        "invocation_available": True,
        "invocation": {
            "operation": "call_mastery_tutor",
            "method": "POST",
            "path": "/api/link/v1/topics/{path_id}/mastery/tool",
            "requires_confirm": True,
            "scope": "Authenticated account, resolved workspace, existing path, and host native tool schema.",
        },
        "record_turn": {
            "name": "record_turn",
            "description": "Persist the tutor's completed response as the assistant message for this external learner turn.",
            "arguments": {},
        },
        "instructions": (
            "Execute the selected registered native Mastery tool, not a DeepTutor model turn. "
            "Keep client_id and session_id stable, reuse turn_id for one learner turn, and "
            "use a new request_id per tool operation; reuse it unchanged for a retry. "
            "Include the learner's verbatim message on the first operation and persist the "
            "completed tutor reply with tool=record_turn. The host executes its original "
            "assessment semantics; mastery_grade, mastery_assess, mastery_prepare_lesson, "
            "and corrected mastery_repair_question may invoke host models as declared. "
            "Source-grounded mastery_quiz may invoke the host model to bind the exact question/key. "
            "Assistant-owned PDF evidence and historical-repair operations are separate and do not call the host chat model."
        ),
        "read_only_status_bridge": {
            "available": True,
            "operation": "read_mastery_status",
            "scope": "One explicitly selected topic; no session, turn, pending answer, or model call.",
        },
        "read_only_paths_bridge": {
            "available": True,
            "operation": "read_mastery_paths",
            "scope": "Current authenticated account/workspace only; no path switching or answers.",
        },
        "model_calls": "No model is invoked by this catalog or read-only status bridges; individual native tools declare their host model behavior.",
    }


_NATIVE_RECEIPT_DDL = """CREATE TABLE IF NOT EXISTS codex_native_tool_requests (
    request_key TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    turn_key TEXT NOT NULL,
    status TEXT NOT NULL,
    response_json TEXT,
    created_at REAL NOT NULL
)"""


def _native_request_key(client_id: str, session_id: str, request_id: str) -> tuple[str, str]:
    identity = hashlib.sha256(
        json.dumps([client_id, session_id], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    key = hashlib.sha256(
        json.dumps([identity, request_id], separators=(",", ":")).encode()
    ).hexdigest()
    return key, identity


def _claim_native_request(store, key: str, digest: str, turn_key: str) -> dict | None:
    """Atomically cache a request or reject an ambiguous in-flight retry."""
    with store._connect() as conn:
        conn.execute(_NATIVE_RECEIPT_DDL)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_hash, status, response_json FROM codex_native_tool_requests WHERE request_key=?",
            (key,),
        ).fetchone()
        if row is not None:
            if row["payload_hash"] != digest:
                raise BridgeError("request_id_conflict", "request_id was already used with different content.", 409)
            if row["status"] == "completed" and row["response_json"]:
                return json.loads(row["response_json"])
            raise BridgeError(
                "request_outcome_unknown",
                "This request may already have reached the host tool. Read mastery status before creating a new request; the same request will not be executed twice.",
                409,
            )
        conn.execute(
            "INSERT INTO codex_native_tool_requests VALUES (?, ?, ?, 'running', NULL, ?)",
            (key, digest, turn_key, time.time()),
        )
        conn.commit()
    return None


def _complete_native_request(store, key: str, response: dict) -> None:
    with store._connect() as conn:
        conn.execute(_NATIVE_RECEIPT_DDL)
        row = conn.execute(
            "SELECT response_json FROM codex_native_tool_requests WHERE request_key=?",
            (key,),
        ).fetchone()
        prior = json.loads(row["response_json"] or "{}") if row else {}
        complete = {**prior, **response}
        conn.execute(
            "UPDATE codex_native_tool_requests SET status='completed', response_json=? WHERE request_key=? AND status='running'",
            (json.dumps(complete, ensure_ascii=False, allow_nan=False), key),
        )


def _external_public_value(value: Any, depth: int = 0) -> Any:
    blocked = {
        "answer",
        "learner_answer",
        "user_answer",
        "expected_answer",
        "correct_answer",
        "answer_key",
        "explanation",
    }
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            str(key)[:100]: _external_public_value(item, depth + 1)
            for key, item in list(value.items())[:200]
            if str(key).casefold() not in blocked
        }
    if isinstance(value, list):
        return [_external_public_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:24000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def _validate_native_value(value: Any, schema: dict, name: str) -> None:
    expected = schema.get("type")
    types = {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "array": list,
        "object": dict,
    }
    expected_types = expected if isinstance(expected, list) else [expected]
    concrete = [types[item] for item in expected_types if item in types]
    if value is None and "null" in expected_types:
        return
    if concrete and not any(isinstance(value, candidate) for candidate in concrete):
        raise BridgeError("invalid_tool_arguments", f"Invalid type for native tool argument {name}.", 422)
    if isinstance(value, bool) and any(item in {"integer", "number"} for item in expected_types):
        raise BridgeError("invalid_tool_arguments", f"Invalid type for native tool argument {name}.", 422)
    if "enum" in schema and value not in schema["enum"]:
        raise BridgeError("invalid_tool_arguments", f"Invalid value for native tool argument {name}.", 422)
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        extra = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if extra and additional is False:
            raise BridgeError("invalid_tool_arguments", f"Unknown native tool arguments: {sorted(extra)}.", 422)
        missing = set(schema.get("required") or ()) - set(value)
        if missing:
            raise BridgeError("invalid_tool_arguments", f"Missing native tool arguments: {sorted(missing)}.", 422)
        for key, child in value.items():
            if key in properties:
                _validate_native_value(child, properties[key], f"{name}.{key}")
            elif isinstance(additional, dict):
                _validate_native_value(child, additional, f"{name}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            _validate_native_value(child, schema["items"], f"{name}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10**9):
            raise BridgeError("invalid_tool_arguments", f"String length is invalid for native tool argument {name}.", 422)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise BridgeError("invalid_tool_arguments", f"Number is out of range for native tool argument {name}.", 422)


async def _save_external_tutor_message(sessions, session_id: str, body, role: str, content: str, events=None) -> None:
    messages = await sessions.get_messages(session_id)
    marker = {
        "client_id": body.client_id,
        "client_session_id": body.client_session_id or body.session_id,
        "turn_id": body.turn_id,
    }
    existing = next(
        (
            row
            for row in messages
            if row.get("role") == role
            and (row.get("metadata") or {}).get("external_mastery_tutor") == marker
        ),
        None,
    )
    if existing is not None:
        if existing.get("content") != content:
            raise BridgeError("turn_message_conflict", f"A different {role} message is already saved for this learner turn.", 409)
        return
    await sessions.add_message(
        session_id,
        role,
        content,
        capability="mastery_path",
        events=events,
        metadata={"external_mastery_tutor": marker},
    )


def _native_tool_model_behavior(name: str, arguments: dict) -> dict:
    if name == "mastery_grade":
        return {"host_model_may_be_invoked": True, "reason": "Semantic grading is used when the stored exact-answer path cannot decide the learner response."}
    if name == "mastery_assess":
        return {"host_model_may_be_invoked": True, "reason": "The host semantically assesses the learner's qualitative explanation."}
    if name == "mastery_quiz":
        return {"host_model_may_be_invoked": True, "reason": "The host validates source-grounded question/key contracts with its configured model."}
    if name == "mastery_prepare_lesson":
        return {"host_model_may_be_invoked": True, "reason": "The host may use its configured model while preparing source-grounded lesson evidence."}
    if name == "mastery_repair_question" and arguments.get("action") == "correct":
        return {"host_model_may_be_invoked": True, "reason": "Correcting an answer key may semantically re-grade the stored learner response."}
    return {"host_model_may_be_invoked": False, "reason": "This native tool contract does not request a model call."}


async def invoke_native_mastery_tool(store, path_id: str, request) -> dict:
    """Execute one confirmed v1.7.1 Mastery BaseTool under host state/lease rules."""
    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    from deeptutor.capabilities.mastery import tools as module
    from deeptutor.core.tool_protocol import BaseTool
    from deeptutor.learning.storage import PathLeaseConflictError
    from deeptutor.services.session import get_session_store

    native = {}
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and value is not BaseTool
            and issubclass(value, BaseTool)
            and value.__module__ == module.__name__
        ):
            instance = value()
            if instance.name in module.MASTERY_TOOL_NAMES:
                native[instance.name] = instance

    client = native.get(request.tool)
    if request.tool != "record_turn" and client is None:
        raise BridgeError("unknown_mastery_tool", "Unknown v1.7.1 native Mastery tool.", 422)
    if request.tool == "record_turn":
        if request.arguments:
            raise BridgeError("invalid_tool_arguments", "record_turn takes no tool arguments.", 422)
        if not request.assistant_message:
            raise BridgeError("invalid_tool_arguments", "record_turn requires assistant_message.", 422)
    elif request.assistant_message is not None:
        raise BridgeError("invalid_tool_arguments", "Persist completed tutor text with tool=record_turn.", 422)

    definition = client.get_definition() if client is not None else None
    schema = definition.to_openai_schema()["function"]["parameters"] if definition else {}
    arguments = dict(request.arguments)
    if client is not None:
        _validate_native_value(arguments, schema, "arguments")

    client_session_id = request.client_session_id or request.session_id
    learner_message = (
        request.user_message
        if request.user_message and request.user_message.strip()
        else None
    )
    if request.tool == "mastery_grade" and not (learner_message and learner_message.strip()):
        submitted_answer = arguments.get("answer")
        submitted_answers = arguments.get("answers")
        has_submitted_answer = bool(
            isinstance(submitted_answer, str) and submitted_answer.strip()
        ) or bool(
            isinstance(submitted_answers, dict)
            and submitted_answers
            and all(isinstance(value, str) and value.strip() for value in submitted_answers.values())
        )
        if not has_submitted_answer:
            interaction_id = str(arguments.get("question_id") or "").strip()
            interaction = (
                store.get_interaction(path_id, interaction_id)
                if interaction_id
                else store.get_active_interaction(path_id)
            )
            committed_answer = False
            if interaction is not None:
                committed = getattr(interaction, "answers", None)
                if isinstance(committed, dict) and committed:
                    committed_answer = all(
                        isinstance(value, str) and value.strip()
                        for value in committed.values()
                    )
                else:
                    committed_answer = bool(
                        isinstance(getattr(interaction, "user_answer", None), str)
                        and interaction.user_answer.strip()
                    )
            if not committed_answer:
                raise BridgeError(
                    "missing_learner_answer",
                    "mastery_grade requires the learner's actual answer; no empty answer was recorded.",
                    422,
                )
    elif learner_message is not None and not learner_message.strip():
        learner_message = None

    key, identity = _native_request_key(request.client_id, client_session_id, request.request_id)
    request_payload = request.model_dump()
    request_payload["session_id"] = client_session_id
    request_payload["client_session_id"] = client_session_id
    digest = hashlib.sha256(
        json.dumps(
            {"path_id": path_id, **request_payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    turn_key = hashlib.sha256(json.dumps([identity, request.turn_id], separators=(",", ":")).encode()).hexdigest()
    operation_id = "external_" + key
    replay = _claim_native_request(store, key, digest, turn_key)
    if replay is not None:
        return {**replay, "replayed": True}

    session_id = "codex_mastery_" + identity[:32]
    try:
        mutation_lock = advisory_lock(mutation_lock_path(store, path_id))
        lock_token = mutation_lock.__enter__()
        await asyncio.to_thread(recover_locked_lease, store, path_id, lock_token)
        await asyncio.to_thread(
            store.acquire_path_lease,
            path_id,
            session_id,
            operation_id,
            bind_session=False,
        )
    except LockBusy as exc:
        with store._connect() as conn:
            conn.execute("DELETE FROM codex_native_tool_requests WHERE request_key=? AND status='running'", (key,))
        raise BridgeError("path_busy", "Another AI assistant learning write is active. Retry later.", 409) from exc
    except PathLeaseConflictError as exc:
        if "mutation_lock" in locals():
            mutation_lock.__exit__(None, None, None)
        with store._connect() as conn:
            conn.execute("DELETE FROM codex_native_tool_requests WHERE request_key=? AND status='running'", (key,))
        raise BridgeError("active_path_turn", "This Mastery path is active in another turn; retry after that turn finishes.", 409) from exc
    except Exception:
        if "mutation_lock" in locals() and "lock_token" in locals():
            mutation_lock.__exit__(None, None, None)
        raise

    try:
        from deeptutor.services.session import get_session_store

        sessions = get_session_store()
        session = await sessions.get_session(session_id)
        if session is None:
            session = await sessions.create_session(title="AI assistant Mastery", session_id=session_id)
        await asyncio.to_thread(store.bind_session, path_id, session_id, owns_path=False)
        preferences = dict(session.get("preferences") or {})
        if preferences.get("mastery_path_id") != path_id:
            await sessions.update_session_preferences(session_id, {"mastery_path_id": path_id})
        mode = request.mode or preferences.get("mastery_session_mode") or "study"

        if learner_message:
            await _save_external_tutor_message(sessions, session_id, request, "user", learner_message)

        if request.tool == "record_turn":
            prior = _external_turn_events(store, turn_key)
            await _save_external_tutor_message(
                sessions, session_id, request, "assistant", request.assistant_message, prior
            )
            result = {
                "tool": "record_turn",
                "success": True,
                "content": "Completed tutor response saved.",
                "metadata": {},
                "status": "saved",
            }
        else:
            arguments.update(
                {
                    "_mastery_path_id": path_id,
                    "_session_id": session_id,
                    "_turn_id": operation_id,
                    "_mastery_session_mode": mode,
                    "_language": request.language or "en",
                    "_end_turn_on_card": lambda: None,
                    "_bind_active_path": lambda value: arguments.update(_mastery_path_id=value),
                    "_bind_active_mode": lambda value: arguments.update(_mastery_session_mode=value),
                    "_profile_recorded": lambda _receipt: None,
                    "_bind_lesson_objective": lambda _objective: None,
                }
            )
            if learner_message:
                arguments["_learner_message"] = learner_message
            native_result = await client.execute(**arguments)
            try:
                raw_content = json.loads(native_result.content)
            except (TypeError, ValueError):
                raw_content = native_result.content
            result = {
                "tool": request.tool,
                "success": bool(native_result.success),
                "content": _external_public_value(raw_content),
                "sources": _external_public_value(native_result.sources),
                "metadata": _external_public_value(native_result.metadata),
                "terminate_turn": bool(native_result.terminate_turn),
                "pause_for_user": _external_public_value(native_result.pause_for_user),
                "model_behavior": _native_tool_model_behavior(request.tool, request.arguments),
            }
            events = [
                {
                    "type": "tool_call",
                    "source": "mastery_path",
                    "stage": "responding",
                    "content": request.tool,
                    "metadata": {"tool_call_id": operation_id, "args": _external_public_value(request.arguments)},
                },
                {
                    "type": "tool_result",
                    "source": "mastery_path",
                    "stage": "responding",
                    "content": json.dumps(result["content"], ensure_ascii=False) if not isinstance(result["content"], str) else result["content"],
                    "metadata": {"tool": request.tool, "tool_call_id": operation_id, "tool_metadata": result["metadata"], "success": result["success"]},
                },
            ]
            await asyncio.to_thread(_save_external_tool_events, store, key, turn_key, events)

        progress = await asyncio.to_thread(store.load, arguments.get("_mastery_path_id", path_id))
        response = {
            **result,
            "session_id": client_session_id,
            "client_session_id": client_session_id,
            "host_session_id": session_id,
            "path_id": arguments.get("_mastery_path_id", path_id),
            "path_revision": progress.version if progress else 0,
            "replayed": False,
        }
        _complete_native_request(store, key, response)
        return response
    finally:
        await asyncio.to_thread(store.release_leases_for_turn, operation_id)
        mutation_lock.__exit__(None, None, None)


def _external_turn_events(store, turn_key: str) -> list[dict]:
    with store._connect() as conn:
        conn.execute(_NATIVE_RECEIPT_DDL)
        rows = conn.execute(
            "SELECT response_json FROM codex_native_tool_requests WHERE turn_key=? AND status='completed' ORDER BY rowid",
            (turn_key,),
        ).fetchall()
    return [event for row in rows if row["response_json"] for event in json.loads(row["response_json"]).get("events", [])]


def _save_external_tool_events(store, key: str, turn_key: str, events: list[dict]) -> None:
    with store._connect() as conn:
        conn.execute(_NATIVE_RECEIPT_DDL)
        row = conn.execute("SELECT response_json FROM codex_native_tool_requests WHERE request_key=?", (key,)).fetchone()
        response = json.loads(row["response_json"] or "{}") if row else {}
        response["events"] = events
        conn.execute(
            "UPDATE codex_native_tool_requests SET response_json=? WHERE request_key=?",
            (json.dumps(response, ensure_ascii=False, allow_nan=False), key),
        )


async def invoke_mastery_paths(store, active_path_id: str = "") -> dict:
    """Run the host's read-only path overview tool in the current workspace."""
    if active_path_id and not store.exists(active_path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)

    from deeptutor.capabilities.mastery.tools import MasteryPathsTool

    result = await MasteryPathsTool().execute(_mastery_path_id=active_path_id)
    if not result.success:
        raise BridgeError("native_tool_refused", "The host could not list mastery paths.")
    try:
        payload = json.loads(result.content)
    except (TypeError, ValueError) as exc:
        raise BridgeError("invalid_native_tool_result", "The host returned an invalid path list.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), list):
        raise BridgeError("invalid_native_tool_result", "The host returned an invalid path list.")

    paths = []
    for row in payload["paths"][:100]:
        if not isinstance(row, dict):
            continue
        paths.append(
            {
                "path_id": str(row.get("path_id") or "")[:200],
                "name": str(row.get("name") or "")[:400],
                "objectives": max(0, int(row.get("objectives") or 0)),
                "mastered": max(0, int(row.get("mastered") or 0)),
                "learning": max(0, int(row.get("learning") or 0)),
                "not_started": max(0, int(row.get("not_started") or 0)),
                "due_reviews": max(0, int(row.get("due_reviews") or 0)),
                "complete": bool(row.get("complete")),
                "open_question": bool(row.get("open_question")),
                "updated_at": row.get("updated_at"),
                "active": bool(row.get("active")),
            }
        )
    return {
        "native_tool": "mastery_paths",
        "active_path_id": active_path_id or None,
        "paths": paths,
        "omitted": max(0, len(payload["paths"]) - len(paths)),
        "untrusted_content": True,
    }


async def invoke_mastery_status(store, path_id: str) -> dict:
    """Run the host's read-only status tool and omit turn/private answer data."""
    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)

    from deeptutor.capabilities.mastery.tools import MasteryStatusTool

    result = await MasteryStatusTool().execute(
        _mastery_path_id=path_id,
        include_teaching_history=False,
        _working_brief=True,
    )
    if not result.success:
        raise BridgeError("native_tool_refused", "The host could not read this mastery status.")
    try:
        payload = json.loads(result.content)
    except (TypeError, ValueError) as exc:
        raise BridgeError("invalid_native_tool_result", "The host returned an invalid status result.") from exc
    if not isinstance(payload, dict):
        raise BridgeError("invalid_native_tool_result", "The host returned an invalid status result.")

    next_step = payload.get("next") or {}
    safe_next_fields = (
        "action",
        "module_id",
        "module_name",
        "knowledge_point_id",
        "knowledge_point_name",
        "knowledge_point_type",
        "status",
        "gate",
        "mastery",
        "threshold",
        "reason",
        "forgetting_risk",
    )
    safe_map = payload.get("map") or {}
    modules = []
    remaining = 80
    for module in safe_map.get("modules", [])[:40]:
        points = module.get("knowledge_points", [])[:remaining]
        remaining -= len(points)
        modules.append(
            {
                **{
                    key: (
                        str(module[key])[:400]
                        if key in {"id", "name", "objective"}
                        else module[key]
                    )
                    for key in ("id", "name", "objective", "order")
                    if key in module
                },
                "knowledge_points": [
                    {
                        key: (
                            [str(value)[:200] for value in point[key][:100]]
                            if key in {"prerequisite_ids", "topic_source_ids"}
                            else str(point[key])[:400]
                            if key in {"id", "name", "type", "status", "mastery_source"}
                            else point[key]
                        )
                        for key in (
                            "id",
                            "name",
                            "type",
                            "prerequisite_ids",
                            "topic_source_ids",
                            "status",
                            "mastery",
                            "mastery_source",
                            "deferred",
                        )
                        if key in point
                    }
                    for point in points
                ],
            }
        )
    profile = payload.get("learner_profile")
    safe_profile = None
    if isinstance(profile, dict):
        safe_profile = {
            key: str(profile[key])[:600]
            for key in (
                "prior_knowledge",
                "target_level",
                "time_budget",
                "preferences",
                "teaching_strategy",
            )
            if key in profile
        }
    pending = (
        payload.get("pending_interaction") is not None
        or next_step.get("action") == "answer_pending"
    )
    counts = safe_map.get("counts") or {}
    safe_counts = {
        key: max(0, int(counts.get(key, 0)))
        for key in ("mastered", "learning", "new", "total")
    }
    safe_next = {}
    for key in safe_next_fields:
        if key not in next_step:
            continue
        value = next_step[key]
        safe_next[key] = value[:600] if isinstance(value, str) else value
    return {
        "native_tool": "mastery_status",
        "path_id": path_id,
        "status": str(payload.get("status") or "")[:40],
        "path_revision": max(0, int(payload.get("path_revision") or 0)),
        "path_name": str(payload.get("path_name") or "")[:400],
        "goal": str(payload.get("goal") or "")[:2000],
        "sources": [
            {
                key: source[key]
                for key in ("id", "kind", "label", "available")
                if key in source
            }
            for source in payload.get("sources", [])[:80]
        ],
        "mode": payload.get("mode"),
        "due_review_ids": [str(value)[:200] for value in payload.get("due_review_ids", [])[:80]],
        "next": safe_next,
        "map": {"counts": safe_counts, "modules": modules},
        "objectives_omitted": max(
            0,
            int((safe_map.get("counts") or {}).get("total", 0))
            - sum(len(module["knowledge_points"]) for module in modules),
        ),
        "learner_profile": safe_profile,
        "intake_needed": payload.get("intake_needed"),
        "pending": {"present": pending, "action": "finish_in_web" if pending else None},
        "untrusted_content": True,
    }


async def run_direct_capability_turn(request, workspace_id: str, path_id: str = "") -> dict:
    """Run one explicitly confirmed host turn through DeepTutorApp."""
    from deeptutor.app import DeepTutorApp

    app = DeepTutorApp(workspace_id=workspace_id)
    if request.capability == "mastery_path":
        if not path_id:
            raise BridgeError("topic_required", "Bind an existing topic before a Mastery turn.", 422)
        from deeptutor.learning.storage import LearningStore

        if not LearningStore().exists(path_id):
            raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    elif path_id:
        raise BridgeError("unexpected_topic", "Chat turns do not accept a Mastery topic.", 422)

    try:
        contract = app.get_capability_contract(request.capability)
    except ValueError as exc:
        raise BridgeError("unknown_capability", "This capability is not registered in this DeepTutor installation.", 404) from exc

    if request.session_id:
        session = await app.get_session(request.session_id)
        if session is None:
            raise BridgeError("unknown_session", "Session not found in this account/workspace.", 404)
        active = await app.get_active_turn(request.session_id)
        if active:
            raise BridgeError("active_turn", "This session already has an active turn; it was not changed.")

    availability = app.get_capability_availability(request.capability)
    if not availability.available:
        raise BridgeError(
            "capability_unavailable",
            "The requested DeepTutor capability is installed but unavailable: "
            + (availability.install_hint or "its runtime requirements are not met."),
            422,
        )
    payload = {
        "content": request.message,
        "client_submission_id": request.request_id,
        "capability": request.capability,
        "capability_once": True,
        "session_id": request.session_id,
        "workspace_id": workspace_id,
        "mastery_path_id": path_id or None,
        "knowledge_bases": request.knowledge_bases,
        "config": request.config,
        "language": request.language,
        "book_references": request.book_references,
        "reading_references": request.reading_references,
        "persist_user_message": True,
    }
    try:
        session, turn = await app.start_turn(payload)
    except Exception as exc:
        # The host owns idempotent submission admission and active-turn conflicts.
        message = str(exc).casefold()
        if "submission" in message or "conflict" in message or "active" in message:
            raise BridgeError("turn_conflict", "This submission conflicts with an existing turn.") from None
        raise BridgeError("turn_start_failed", "DeepTutor could not start the requested turn.") from None

    result = await collect_direct_turn_events(app, str(turn["id"]))
    return {
        "capability": request.capability,
        "capability_kind": contract.get("kind", "turn"),
        "capability_model_behavior": "The host capability controls model usage; explicit invocation is not a promise of model-free execution.",
        "session_id": str(session.get("id") or ""),
        "turn_id": str(turn.get("id") or ""),
        **result,
    }


async def reply_to_direct_turn(
    app, session_id: str, turn_id: str, request_id: str, text: str, after_seq: int
) -> dict:
    """Submit an explicit user reply only to a turn currently awaiting input."""
    session = await app.get_session(session_id)
    if session is None:
        raise BridgeError("unknown_session", "Session not found in this account/workspace.", 404)
    active = await app.get_active_turn(session_id)
    if not active or str(active.get("id") or active.get("turn_id") or "") != turn_id:
        raise BridgeError("inactive_turn", "This turn is no longer waiting for a reply.", 409)
    if str(active.get("status") or "") != "waiting_input":
        raise BridgeError("turn_not_waiting", "DeepTutor is not waiting for a reply on this turn.", 409)
    accepted = await app.turns.submit_user_reply(turn_id, text=text, command_id=request_id)
    if not accepted:
        raise BridgeError("reply_not_accepted", "DeepTutor is not waiting for a reply on this turn.", 409)
    result = await collect_direct_turn_events(app, turn_id, after_seq)
    return {"session_id": session_id, "turn_id": turn_id, **result}


async def read_session_history(app, session_id: str, offset: int = 0, limit: int = 20) -> dict:
    """Return a bounded, user-visible page from one explicitly requested session."""
    if offset < 0 or not 1 <= limit <= 50:
        raise BridgeError("invalid_page", "Use offset >= 0 and limit from 1 to 50.", 422)
    session = await app.get_session(session_id)
    if session is None:
        raise BridgeError("unknown_session", "Session not found in this account/workspace.", 404)
    messages = session.get("messages", []) if isinstance(session, dict) else []
    visible = []
    for row in messages:
        if not isinstance(row, dict) or row.get("role") not in {"user", "assistant"}:
            continue
        visible.append(
            {
                "id": str(row.get("id") or "")[:100],
                "role": row["role"],
                "content": str(row.get("content") or "")[:6000],
                "created_at": row.get("created_at"),
            }
        )
    page = visible[offset : offset + limit]
    return {
        "session": {
            key: str(session[key])[:300]
            for key in ("id", "title", "capability", "updated_at")
            if session.get(key) is not None
        },
        "messages": page,
        "total_messages": len(visible),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < len(visible) else None,
        "untrusted_content": True,
    }


async def collect_direct_turn_events(app, turn_id: str, after_seq: int = 0) -> dict:
    text_parts: list[str] = []
    sources: list[dict] = []
    cards: list[dict] = []
    tool_names: set[str] = set()
    state = "running"
    last_seq = after_seq
    async for event in app.stream_turn(turn_id, after_seq=after_seq):
        last_seq = max(last_seq, int(event.get("seq") or 0))
        event_type = event.get("type")
        if event_type == "content":
            text_parts.append(str(event.get("content") or ""))
        elif event_type == "sources":
            for row in (event.get("metadata") or {}).get("sources", [])[:40]:
                if isinstance(row, dict):
                    sources.append(
                        {
                            key: str(row[key])[:500]
                            for key in ("source_id", "title", "label", "locator", "url")
                            if row.get(key) is not None
                        }
                    )
        elif event_type == "tool_call":
            name = str(event.get("content") or "")
            if name.startswith("mastery_"):
                tool_names.add(name[:100])
        elif event_type == "tool_result":
            metadata = event.get("metadata") or {}
            tool_metadata = metadata.get("tool_metadata") or {}
            card = tool_metadata.get("mastery_question")
            if isinstance(card, dict):
                cards.append(_public_question_card(card))
        elif event_type == "wait_for_input":
            state = "waiting_for_user"
            break
        elif event_type == "error":
            state = "failed"
        elif event_type == "done":
            state = str((event.get("metadata") or {}).get("status") or "completed")
            break
    return {
        "status": state,
        "content": "".join(text_parts)[-24000:],
        "sources": sources[:40],
        "question_cards": cards[:8],
        "mastery_tools_used": sorted(tool_names),
        "last_seq": last_seq,
        "persisted_in_deeptutor": True,
        "untrusted_content": True,
    }


def _public_question_card(card: dict) -> dict:
    allowed = {
        "question_id",
        "prompt",
        "question_type",
        "objective",
        "difficulty",
        "attempt",
        "allow_free_text",
        "options",
        "visual",
        "questions",
    }
    result = {key: card[key] for key in allowed if key in card}
    if isinstance(result.get("options"), list):
        result["options"] = [
            {key: str(option[key])[:2000] for key in ("label", "body") if key in option}
            for option in result["options"][:20]
            if isinstance(option, dict)
        ]
    if isinstance(result.get("questions"), list):
        result["questions"] = [_public_question_card(item) for item in result["questions"][:4] if isinstance(item, dict)]
    for key in ("question_id", "prompt", "question_type", "difficulty"):
        if key in result:
            result[key] = str(result[key])[:4000]
    return result


def update_review_settings(store, path_id: str, desired_retention: float, expected_revision: int) -> dict:
    if not 0.7 <= desired_retention <= 0.99:
        raise BridgeError("invalid_retention", "desired_retention must be between 0.70 and 0.99.", 422)
    with assessment_lease(store, path_id) as turn_id:
        with store.transaction(path_id) as tx:
            if tx.base_revision != expected_revision:
                raise BridgeError("stale_revision", "Topic changed; refresh before changing review settings.", 409)
            if tx.active_interaction() or tx.progress.pending_question:
                raise BridgeError("pending_interaction", "Finish the pending question in the original website.")
            from deeptutor.learning.review_strategy import scheduler_for_progress

            if tx.progress.desired_retention != desired_retention or any(
                state.desired_retention != desired_retention
                for state in tx.progress.repetition_states.values()
            ):
                scheduler_for_progress(tx.progress).set_desired_retention(tx.progress, desired_retention)
                tx.touch()
                tx.emit("review.settings_changed", {"desired_retention": desired_retention}, session_id="__codex__", turn_id=turn_id)
        return {"topic_id": path_id, "revision": tx.progress.version, "desired_retention": tx.progress.desired_retention, "saved": tx.changed}


def set_learner_override(store, path_id: str, objective_id: str, mastered: bool, note: str, expected_revision: int) -> dict:
    if type(mastered) is not bool:
        raise BridgeError("invalid_mastery_claim", "mastered must be a boolean learner declaration.", 422)
    from deeptutor.learning.models import LearnerMasteryOverride
    from deeptutor.learning.policy import find_knowledge_point

    with assessment_lease(store, path_id) as turn_id:
        with store.transaction(path_id) as tx:
            if tx.base_revision != expected_revision:
                raise BridgeError("stale_revision", "Topic changed; refresh before recording a learner declaration.", 409)
            if tx.active_interaction() or tx.progress.pending_question:
                raise BridgeError("pending_interaction", "Finish the pending question in the original website.")
            kp, _, _ = find_knowledge_point(tx.progress, objective_id)
            if kp is None:
                raise BridgeError("unknown_objective", "Objective not found on this topic.", 404)
            current = tx.progress.learner_mastery_overrides.get(objective_id)
            if mastered:
                replacement = LearnerMasteryOverride(knowledge_point_id=objective_id, note=note[:500].strip())
                changed = current != replacement
                if changed:
                    tx.progress.learner_mastery_overrides[objective_id] = replacement
                    tx.progress.deferred_objectives.pop(objective_id, None)
                    tx.emit("mastery.overridden", {"knowledge_point_id": objective_id, "mastered": True}, session_id="__codex__", turn_id=turn_id)
            else:
                changed = current is not None
                if changed:
                    tx.progress.learner_mastery_overrides.pop(objective_id, None)
                    tx.emit("mastery.override_cleared", {"knowledge_point_id": objective_id, "mastered": False}, session_id="__codex__", turn_id=turn_id)
            if changed:
                tx.touch()
        return {"topic_id": path_id, "revision": tx.progress.version, "objective_id": objective_id, "mastered": mastered, "source": "learner_declaration", "saved": changed}
