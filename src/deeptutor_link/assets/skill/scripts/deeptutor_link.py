#!/usr/bin/env python3
"""Standalone Skill client: Python 3.11+ standard library, no installation."""

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class CommandError(ValueError):
    """A diagnostic that contains neither submitted content nor credentials."""


def origin(raw):
    value = urllib.parse.urlsplit(raw)
    if value.scheme not in {"http", "https"} or not value.hostname:
        raise ValueError("Use an absolute HTTP(S) origin.")
    if (
        value.username
        or value.password
        or value.query
        or value.fragment
        or value.path not in {"", "/"}
    ):
        raise ValueError("Origin must not include credentials, path, query or fragment.")
    if value.scheme == "http" and value.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote connections require HTTPS; use an SSH tunnel for HTTP.")
    return raw.rstrip("/")


def load(path):
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Configuration and credentials require chmod 600 and no symlinks.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def save(path, value):
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError("Refuse symbolic-link configuration.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dtlink-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config, credentials):
        self.config = config
        self.base = origin(config["url"])
        self.web = origin(config.get("web_url", self.base))
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.headers = {
            "X-DeepTutor-Workspace": config.get("workspace_id", ""),
            "Accept": "application/json",
        }
        if config.get("account_id"):
            self.headers["X-DeepTutor-Account"] = config["account_id"]
        token = os.environ.get(config.get("token_env", "DT_LINK_TOKEN"), "")
        if token:
            self.headers["Authorization"] = "Bearer " + token
        elif credentials.exists():
            cookie = load(credentials).get(self.base)
            if cookie:
                self.headers["Cookie"] = "dt_token=" + cookie

    def call(self, method, path, data=None, params=None, timeout=60):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = dict(self.headers)
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            if len(body) > 65536:
                raise ValueError("Request exceeds 64 KiB.")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(1048577)
        except urllib.error.HTTPError as exc:
            code = "request_failed"
            try:
                detail = json.loads(exc.read(65536)).get("detail")
                if isinstance(detail, dict):
                    code = str(detail.get("code", code))
            except (ValueError, AttributeError):
                pass
            raise CommandError(
                f"DeepTutor HTTP {exc.code} ({code}); refresh context or run doctor."
            ) from None
        except urllib.error.URLError:
            raise CommandError("DeepTutor connection failed; check origin and TLS.") from None
        if len(raw) > 1048576:
            raise ValueError("DeepTutor response exceeds 1 MiB.")

        def links(value):
            if isinstance(value, list):
                return [links(v) for v in value]
            if isinstance(value, dict):
                return {
                    k: self.web + v
                    if k == "web_url"
                    and isinstance(v, str)
                    and v.startswith("/")
                    and not v.startswith("//")
                    else links(v)
                    for k, v in value.items()
                }
            return value

        return links(json.loads(raw))

    def call_image(self, path, *, limit=6 * 1024 * 1024, timeout=60):
        headers = dict(self.headers)
        headers["Accept"] = "image/png, image/jpeg"
        request = urllib.request.Request(self.base + path, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {"image/png", "image/jpeg"}:
                    raise CommandError("DeepTutor did not return a supported PDF page image.")
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            code = "request_failed"
            try:
                detail = json.loads(exc.read(65536)).get("detail")
                if isinstance(detail, dict):
                    code = str(detail.get("code", code))
            except (ValueError, AttributeError):
                pass
            raise CommandError(f"DeepTutor HTTP {exc.code} ({code}); refresh context or run doctor.") from None
        except urllib.error.URLError:
            raise CommandError("DeepTutor connection failed; check origin and TLS.") from None
        if len(raw) > limit:
            raise CommandError("PDF page image exceeds the local transfer limit.")
        return content_type, raw

    def topic_path(self, suffix):
        topic = self.config.get("topic_id")
        if not topic:
            raise ValueError("Select an existing topic with bind --topic ID.")
        return "/api/link/v1/topics/" + urllib.parse.quote(topic, safe="") + "/" + suffix


def operation(method, path, required=(), optional=(), query=(), body=(), confirm=False, reason="", scope=""):
    return {
        "method": method,
        "path": path,
        "required": tuple(required),
        "optional": tuple(optional),
        "query": tuple(query),
        "body": tuple(body),
        "confirm": confirm,
        "reason": reason,
        "scope": scope,
        "available": not bool(reason),
    }


OPS = {
    "deeptutor_status": operation("GET", "/health/ready"),
    "list_deeptutor_capabilities": operation("GET", "/api/capabilities/registered"),
    "learning_overview": operation("GET", "@overview", optional=("timezone",)),
    "list_courses": operation("GET", "", reason="DeepTutor v1.7.1 has no course/syllabus API."),
    "get_course_state": operation("GET", "", ("course_id",), reason="DeepTutor v1.7.1 has no course/syllabus API."),
    "list_course_resource_candidates": operation("GET", "", reason="DeepTutor v1.7.1 has no course/syllabus API."),
    "replace_course_syllabus": operation("PUT", "", ("course_id", "units"), body=("units",), confirm=True, reason="DeepTutor v1.7.1 has no course/syllabus API."),
    "set_course_unit_covered": operation("PATCH", "", ("course_id", "unit_id", "covered"), body=("covered",), confirm=True, reason="DeepTutor v1.7.1 has no course/syllabus API."),
    "attach_course_resource": operation("POST", "", ("course_id", "kind", "ref_id"), optional=("label",), body=("kind", "ref_id", "label"), confirm=True, reason="DeepTutor v1.7.1 has no course/syllabus API."),
    "list_mastery_paths": operation("GET", "/api/mastery-paths/topics/index", optional=("limit", "offset")),
    "get_mastery_path": operation("GET", "@mastery_path", ("path_id",)),
    "get_mastery_tutor_tools": operation("GET", "/api/link/v1/mastery/tools"),
    "get_teaching_persona": operation(
        "GET",
        "/api/link/v1/teaching/personas/{name}",
        ("name",),
        optional=("include_content",),
        query=("include_content",),
        scope="Reads one explicitly named visible persona through the original own-over-admin resolution.",
    ),
    "get_teaching_session_selection": operation(
        "GET",
        "/api/link/v1/teaching/sessions/{session_id}/selection",
        ("session_id",),
        scope="Reads persona/mode/language preferences from one exact authorized session; never guesses the latest chat.",
    ),
    "get_teaching_protocol": operation(
        "GET",
        "/api/link/v1/topics/{path_id}/teaching/protocol",
        ("path_id",),
        optional=("persona", "mode", "language", "known_fingerprint"),
        query=("persona", "mode", "language", "known_fingerprint"),
        scope="Reads whitelisted persona and Mastery teaching sections and the AI assistant operation adaptation; does not assemble private system prompts or write learning state.",
    ),
    "call_mastery_tutor": operation(
        "POST",
        "@mastery_turn",
        ("path_id", "turn_id", "request_id", "tool", "confirm"),
        optional=("client_id", "session_id", "client_session_id", "arguments", "user_message", "assistant_message", "mode", "language"),
        body=("client_id", "session_id", "client_session_id", "turn_id", "request_id", "tool", "arguments", "user_message", "assistant_message", "mode", "language", "confirm"),
        confirm=True,
        scope="Executes one explicitly confirmed registered native Mastery BaseTool through its original host implementation, with host session, path lease, and request receipt semantics; individual tools may invoke host models as declared in get_mastery_tutor_tools.",
    ),
    "read_mastery_status": operation(
        "GET",
        "@mastery_status_bridge",
        scope="Uses only the topic explicitly selected by connect/initialize/bind; read-only and answer-redacted.",
    ),
    "read_mastery_paths": operation(
        "GET",
        "/api/link/v1/mastery/paths",
        optional=("active_path_id",),
        query=("active_path_id",),
        scope="Reads path summaries from the current authenticated workspace.",
    ),
    "replace_mastery_outline": operation("POST", "@route_preview", ("request_id", "expected_revision", "modules"), body=("request_id", "expected_revision", "modules"), confirm=True),
    "get_mastery_review_settings": operation("GET", "/api/link/v1/topics/{path_id}/review-settings", ("path_id",)),
    "set_mastery_review_settings": operation("PUT", "/api/link/v1/topics/{path_id}/review-settings", ("path_id", "desired_retention", "expected_revision"), body=("desired_retention", "expected_revision"), confirm=True),
    "get_mastery_objective": operation("GET", "/api/link/v1/topics/{path_id}/objectives/{knowledge_point_id}", ("path_id", "knowledge_point_id")),
    "list_mastery_events": operation("GET", "/api/link/v1/topics/{path_id}/events", ("path_id",), optional=("after_revision",), query=("after_revision",)),
    "set_mastery_learner_override": operation("POST", "/api/link/v1/topics/{path_id}/objectives/{knowledge_point_id}/learner-override", ("path_id", "knowledge_point_id", "mastered", "expected_revision"), optional=("note",), body=("mastered", "note", "expected_revision"), confirm=True),
    "list_question_entries": operation("GET", "/api/question-notebook/entries", optional=("limit", "offset", "mistakes_only", "bookmarked", "is_correct", "search"), query=("limit", "offset", "mistakes_only", "bookmarked", "is_correct", "search")),
    "get_question_entry": operation("GET", "/api/question-notebook/entries/{entry_id}", ("entry_id",)),
    "upsert_question_entry": operation("POST", "/api/question-notebook/entries/upsert", ("question_id", "question", "origin_ref"), optional=("correct_answer", "explanation", "question_type", "options", "session_id", "turn_id"), body=("question_id", "question", "origin_type", "origin_ref", "correct_answer", "explanation", "question_type", "options", "session_id", "turn_id"), confirm=True),
    "update_question_entry": operation("PATCH", "/api/question-notebook/entries/{entry_id}", ("entry_id",), optional=("bookmarked", "followup_session_id", "ai_judgment", "resolved"), body=("bookmarked", "followup_session_id", "ai_judgment", "resolved"), confirm=True),
    "delete_question_entry": operation("DELETE", "/api/question-notebook/entries/{entry_id}", ("entry_id",), confirm=True),
    "practice_summary": operation("GET", "/api/question-notebook/practice/summary", optional=("timezone",), query=("timezone",)),
    "practice_queue": operation("GET", "/api/question-notebook/practice/queue", optional=("timezone", "category_id", "limit"), query=("timezone", "category_id", "limit")),
    "get_practice_question": operation("GET", "/api/question-notebook/practice/questions/{entry_id}", ("entry_id",)),
    "check_practice_answer": operation("POST", "/api/question-notebook/practice/questions/{entry_id}/check", ("entry_id", "answer"), body=("answer",)),
    "record_practice_review": operation("POST", "/api/question-notebook/practice/questions/{entry_id}/review", ("entry_id", "request_id", "version", "rating", "answer", "confirm"), optional=("self_report",), body=("request_id", "version", "rating", "answer", "self_report"), confirm=True, scope="Stores the user's explicitly requested answer and spaced-review rating in the original practice service."),
    "practice_analytics": operation("GET", "/api/question-notebook/practice/analytics", optional=("timezone", "days"), query=("timezone", "days")),
    "list_question_categories": operation("GET", "/api/question-notebook/categories"),
    "list_books": operation("GET", "/api/dashboard/learning-library/books"),
    "get_book_spine": operation("GET", "/api/books/{book_id}/spine", ("book_id",)),
    "get_book_page": operation("GET", "/api/books/{book_id}/pages/{page_id}", ("book_id", "page_id")),
    "list_book_learning_captures": operation("GET", "/api/books/{book_id}/learning-captures", ("book_id",), optional=("status",), query=("status",)),
    "list_reading_materials": operation("GET", "/api/reading/materials"),
    "get_reading_material": operation("GET", "/api/reading/materials/{material_id}", ("material_id",)),
    "get_reading_unit": operation("GET", "/api/link/v1/reading/materials/{material_id}/units/{locator}/slice", ("material_id", "locator"), optional=("offset", "max_chars"), query=("offset", "max_chars")),
    "list_reading_annotations": operation("GET", "/api/reading/materials/{material_id}/annotations", ("material_id",)),
    "save_reading_annotation": operation("PUT", "/api/reading/materials/{material_id}/annotations", ("material_id", "locator", "kind"), optional=("annotation_id", "color", "quote", "note", "source_anchor"), body=("locator", "kind", "annotation_id", "color", "quote", "note", "source_anchor"), confirm=True),
    "delete_reading_annotation": operation("DELETE", "/api/reading/materials/{material_id}/annotations/{annotation_id}", ("material_id", "annotation_id"), confirm=True),
    "list_reading_bookmarks": operation("GET", "/api/reading/materials/{material_id}/bookmarks", ("material_id",)),
    "add_reading_bookmark": operation("POST", "/api/reading/materials/{material_id}/bookmarks", ("material_id", "locator"), optional=("label", "source_anchor"), body=("locator", "label", "source_anchor"), confirm=True),
    "delete_reading_bookmark": operation("DELETE", "/api/reading/materials/{material_id}/bookmarks/{bookmark_id}", ("material_id", "bookmark_id"), confirm=True),
    "list_notebooks": operation("GET", "/api/notebooks"),
    "get_notebook": operation("GET", "/api/link/v1/notebooks/{notebook_id}", ("notebook_id",), optional=("offset", "limit"), query=("offset", "limit")),
    "create_notebook": operation("POST", "/api/notebooks", ("name",), optional=("description", "color", "icon"), body=("name", "description", "color", "icon"), confirm=True),
    "add_notebook_record": operation("POST", "/api/notebooks/actions/add-record", ("notebook_id", "title", "note", "summary"), body=("title", "note", "summary"), confirm=True, scope="Saves only the supplied learning note; surrounding AI assistant messages are not copied."),
    "update_notebook_record": operation("PUT", "/api/notebooks/{notebook_id}/records/{record_id}", ("notebook_id", "record_id"), optional=("title", "summary", "metadata"), body=("title", "summary", "metadata"), confirm=True),
    "delete_notebook_record": operation("DELETE", "/api/notebooks/{notebook_id}/records/{record_id}", ("notebook_id", "record_id"), confirm=True),
    "delete_notebook": operation("DELETE", "/api/notebooks/{notebook_id}", ("notebook_id",), confirm=True),
    "list_knowledge_bases": operation("GET", "/api/knowledge-bases"),
    "list_knowledge_files": operation("GET", "/api/link/v1/knowledge-bases/{kb_name}/files", ("kb_name",), optional=("name_contains", "suffix", "include_hidden", "limit", "offset"), query=("name_contains", "suffix", "include_hidden", "limit", "offset")),
    "read_knowledge_file": operation("GET", "/api/link/v1/knowledge-bases/{kb_name}/file-slice", ("kb_name", "filename"), optional=("offset", "max_chars"), query=("filename", "offset", "max_chars")),
    "search_knowledge_file": operation("GET", "/api/link/v1/knowledge-bases/{kb_name}/file-search-literal", ("kb_name", "filename", "query"), optional=("limit",), query=("filename", "query", "limit")),
    "list_deeptutor_sessions": operation("GET", "/api/sessions", optional=("limit", "offset"), query=("limit", "offset")),
    "get_deeptutor_session": operation("GET", "/api/link/v1/sessions/{session_id}/history", ("session_id",), optional=("offset", "limit"), query=("offset", "limit"), scope="Explicit on-demand page of user/assistant text only; no tool arguments or answer keys."),
    "get_deeptutor_memory_doc": operation("GET", "/api/link/v1/memory/doc/{layer}/{key}", ("layer", "key"), optional=("offset", "max_chars"), query=("offset", "max_chars")),
    "send_message_to_deeptutor": operation(
        "POST",
        "@chat_turn",
        ("capability", "message", "request_id", "direct_dt_conversation"),
        optional=("session_id", "knowledge_bases", "config", "language", "book_references", "reading_references"),
        body=("capability", "message", "request_id", "session_id", "knowledge_bases", "config", "language", "book_references", "reading_references", "direct_dt_conversation", "confirm"),
        confirm=True,
        scope="Starts one explicitly confirmed turn in a capability registered and available in this DeepTutor installation; host capability configuration, authorization, session/turn hooks, and model behavior remain authoritative.",
    ),
    "reply_to_deeptutor_turn": operation(
        "POST",
        "/api/link/v1/capability/turn/reply",
        ("session_id", "turn_id", "request_id", "message"),
        optional=("after_seq",),
        body=("session_id", "turn_id", "request_id", "message", "after_seq", "confirm"),
        confirm=True,
        scope="Replies only to the explicitly identified DeepTutor turn waiting for input.",
    ),
    "search_vector_knowledge": operation("POST", "@retrieve", ("objective_id", "source_id", "query"), optional=("top_k",), body=("objective_id", "source_id", "query", "top_k")),
    "list_objective_pdfs": operation("GET", "@objective_pdfs", ("objective_id",), optional=("directory",), query=("objective_id", "directory"), scope="Lists only PDFs inside this objective's selected original materials. Does not use embeddings or modify the index."),
    "prepare_pdf_evidence": operation("POST", "@pdf_prepare", ("request_id", "expected_revision", "objective_id", "pages"), optional=("directory",), body=("request_id", "expected_revision", "objective_id", "directory", "pages"), scope="Creates an expiring, source-digest-bound preparation and renders at most six original selected pages. Open each returned image locally; the server cannot attest that pixels were inspected."),
    "commit_pdf_evidence": operation("POST", "@pdf_commit", ("request_id", "preparation_id", "expected_revision", "objective_id", "purpose", "coverage_summary", "coverage_complete", "sources", "confirm"), body=("request_id", "preparation_id", "expected_revision", "objective_id", "purpose", "coverage_summary", "coverage_complete", "sources", "confirm"), confirm=True, scope="Revalidates original page digests, selected scope, exact quotes, preparation references and revision before writing the source packet into the original LearningStore."),
    "inspect_question_repair": operation("GET", "@question_repair_inspect", ("question_id",), scope="Reads one exact completed leaf question, its historical answer and assessment for a repair decision."),
    "preview_question_repair": operation("POST", "@question_repair_preview", ("request_id", "expected_revision", "question_id", "action", "reason"), optional=("expected_answer", "result", "confidence", "feedback", "citations"), body=("request_id", "expected_revision", "question_id", "action", "reason", "expected_answer", "result", "confidence", "feedback", "citations"), scope="Stores a one-hour reviewable preview; does not mutate learning state."),
    "commit_question_repair": operation("POST", "@question_repair_commit", ("request_id", "preview_id", "confirm"), body=("request_id", "preview_id", "confirm"), confirm=True, scope="Commits only the exact confirmed preview after revision, answer and source checks; uses host repair/recomputation where available."),
}


def scrub_answers(value):
    if isinstance(value, list):
        return [scrub_answers(item) for item in value]
    if isinstance(value, dict):
        return {
            key: scrub_answers(item)
            for key, item in value.items()
            if key not in {
                "correct_answer",
                "expected_answer",
                "learner_answer",
                "user_answer",
                "answer_key",
                "explanation",
            }
        }
    return value


def parser():
    p = argparse.ArgumentParser(description="DeepTutor-Link Skill client (stdlib only).")
    root = Path.home() / ".config/deeptutor-link"
    p.add_argument("--config", type=Path, default=root / "connection.json")
    p.add_argument("--credentials", type=Path, default=root / "credentials.json")
    p.add_argument("--cache", type=Path, default=root / "teaching-protocol.json")
    commands = p.add_subparsers(dest="command", required=True)
    for action in ("identity", "connect", "login", "logout"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--url", required=True)
        if action in {"identity", "connect"}:
            cmd.add_argument("--workspace", required=action == "connect", default="")
            cmd.add_argument("--token-env", default="DT_LINK_TOKEN")
        if action == "connect":
            cmd.add_argument("--account", required=True)
            cmd.add_argument("--web-url")
            cmd.add_argument("--topic")
        if action == "login":
            cmd.add_argument("--username", required=True)
    cmd = commands.add_parser("bind")
    cmd.add_argument("--topic", required=True)
    for action in ("doctor", "records", "workspaces", "recover", "route", "catalog", "skills", "capabilities"):
        commands.add_parser(action)
    commands.add_parser("persona-list")
    cmd = commands.add_parser("persona-show")
    cmd.add_argument("name")
    cmd.add_argument("--metadata-only", action="store_true")
    cmd = commands.add_parser("persona-select")
    cmd.add_argument("name")
    commands.add_parser("persona-clear")
    cmd = commands.add_parser("persona-restore")
    cmd.add_argument("--session", required=True)
    cmd = commands.add_parser("protocol")
    cmd.add_argument("--persona")
    cmd.add_argument("--clear-persona", action="store_true")
    cmd.add_argument("--mode", choices=("outline", "study", "review"))
    cmd.add_argument("--language")
    cmd.add_argument("--refresh", action="store_true")
    cmd.add_argument("--new-chat", action="store_true")
    cmd = commands.add_parser("resume")
    cmd.add_argument("--persona")
    cmd.add_argument("--mode", choices=("outline", "study", "review"))
    cmd.add_argument("--language")
    cmd.add_argument("--new-chat", action="store_true")
    cmd = commands.add_parser("skill-read")
    cmd.add_argument("name")
    cmd.add_argument("--path", default="SKILL.md")
    cmd.add_argument("--offset", type=int, default=0)
    cmd.add_argument("--max-chars", type=int, default=12000)
    cmd = commands.add_parser("initialize")
    cmd.add_argument("--url", required=True)
    cmd.add_argument("--account")
    cmd.add_argument("--workspace")
    cmd.add_argument("--web-url")
    cmd.add_argument("--token-env")
    topic_group = cmd.add_mutually_exclusive_group()
    topic_group.add_argument("--topic")
    topic_group.add_argument("--clear-topic", action="store_true")
    cmd = commands.add_parser("history-list")
    cmd.add_argument("--offset", type=int, default=0)
    cmd.add_argument("--limit", type=int, default=20)
    cmd = commands.add_parser("history-show")
    cmd.add_argument("--session", required=True)
    cmd.add_argument("--offset", type=int, default=0)
    cmd.add_argument("--limit", type=int, default=20)
    cmd = commands.add_parser("discover")
    cmd.add_argument("--url", required=True)
    cmd.add_argument("--web-url")
    cmd.add_argument("--token-env", default="DT_LINK_TOKEN")
    for action in ("topics", "context"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--offset", type=int, default=0)
        cmd.add_argument("--limit", type=int, default=20 if action == "topics" else 40)
    cmd = commands.add_parser("retrieve")
    cmd.add_argument("--objective", required=True)
    cmd.add_argument("--source", required=True)
    cmd.add_argument("--query", required=True)
    cmd.add_argument("--top-k", type=int, default=5)
    for action in ("assess", "route-create", "route-preview", "route-commit"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--file", required=True, help="JSON file or '-' for stdin.")
    cmd = commands.add_parser("tool")
    cmd.add_argument("name", choices=sorted(OPS))
    cmd.add_argument("--file", required=True, help="JSON object file or '-' for stdin.")
    cmd = commands.add_parser("pdf-image")
    cmd.add_argument("--preparation", required=True)
    cmd.add_argument("--page", required=True, type=int)
    cmd.add_argument("--output", required=True, type=Path)
    return p


def input_object(filename):
    if filename == "-":
        raw = sys.stdin.read(65537)
    else:
        with Path(filename).open() as stream:
            raw = stream.read(65537)
    if len(raw.encode()) > 65536:
        raise ValueError("Request exceeds 64 KiB.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Request must be a JSON object.")
    return value


def op_path(spec, args):
    path = spec["path"]
    for key in spec["required"]:
        if key not in args:
            raise ValueError("Missing operation argument.")
    for key, value in args.items():
        if key not in {*spec["required"], *spec["optional"], "confirm"}:
            raise ValueError("Unknown operation argument.")
        marker = "{" + key + "}"
        if marker in path:
            path = path.replace(marker, urllib.parse.quote(str(value), safe=""))
    if "{" in path or "}" in path:
        raise ValueError("Operation target is incomplete.")
    return path


def operation_call(client, name, args):
    spec = OPS[name]
    if not spec["available"]:
        return {"available": False, "reason": spec["reason"]}
    allowed = {*spec["required"], *spec["optional"], "confirm"}
    if any(key not in args for key in spec["required"]):
        raise ValueError("Missing operation argument.")
    if any(key not in allowed for key in args):
        raise ValueError("Unknown operation argument.")
    if spec["confirm"] and args.get("confirm") is not True:
        raise ValueError("This operation changes DeepTutor data; explicit confirm=true is required.")
    path = spec["path"]
    data = {key: args[key] for key in spec["body"] if key in args}
    params = {key: args[key] for key in spec["query"] if key in args}
    if name == "read_mastery_status":
        return client.call("GET", client.topic_path("mastery/status"))
    if name == "read_mastery_paths":
        return client.call("GET", path, params=params)
    if name == "list_objective_pdfs":
        return client.call("GET", client.topic_path("pdfs"), params=params)
    if name == "prepare_pdf_evidence":
        return client.call("POST", client.topic_path("pdf-preparations"), data, timeout=120)
    if name == "commit_pdf_evidence":
        return client.call("POST", client.topic_path("pdf-evidence/commits"), data, timeout=120)
    if name == "inspect_question_repair":
        question_id = urllib.parse.quote(str(args["question_id"]), safe="")
        return client.call("GET", client.topic_path(f"questions/{question_id}/repair"))
    if name == "preview_question_repair":
        return client.call("POST", client.topic_path("question-repairs/previews"), data)
    if name == "commit_question_repair":
        return client.call("POST", client.topic_path("question-repairs/commits"), data)
    if name == "call_mastery_tutor":
        client_session_id = args.get("client_session_id")
        session_id = args.get("session_id")
        if not client_session_id and not session_id:
            raise ValueError("client_session_id is required.")
        if client_session_id and session_id and client_session_id != session_id:
            raise ValueError("client_session_id and session_id must match.")
        path = "/api/link/v1/topics/" + urllib.parse.quote(str(args["path_id"]), safe="") + "/mastery/tool"
        data["confirm"] = True
        return client.call("POST", path, data, timeout=120)
    if name == "send_message_to_deeptutor":
        capability = str(args["capability"])
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", capability):
            raise ValueError("Invalid capability name.")
        if capability == "mastery_path":
            path = client.topic_path("capability/turn")
        else:
            path = "/api/link/v1/capability/turn"
        data["confirm"] = True
        return client.call("POST", path, data, timeout=300)
    if name == "reply_to_deeptutor_turn":
        data["confirm"] = True
        return client.call("POST", path, data)
    if name == "learning_overview":
        zone = args.get("timezone", "Asia/Shanghai")
        return {
            "mastery_paths": client.call("GET", "/api/link/v1/topics"),
            "reading_records": client.call("GET", "/api/mastery-paths/reading/records"),
            "practice": client.call("GET", "/api/question-notebook/practice/summary", params={"timezone": zone}),
            "courses": {"available": False, "reason": OPS["list_courses"]["reason"]},
        }
    if name == "list_mastery_paths":
        limit = args.get("limit", 20)
        offset = args.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer.")
        result = client.call("GET", path)
        rows = result.get("topics", []) if isinstance(result, dict) else []
        return {
            "topics": rows[offset : offset + limit],
            "total": len(rows),
            "next_offset": offset + limit if offset + limit < len(rows) else None,
        }
    if name == "get_mastery_path":
        path = "/api/link/v1/topics/" + urllib.parse.quote(str(args["path_id"]), safe="") + "/route"
    elif name.startswith("course_") or name in {"list_courses", "get_course_state", "list_course_resource_candidates"}:
        return {"available": False, "reason": spec["reason"]}
    elif name == "replace_mastery_outline":
        if args.get("confirm") is not True:
            raise ValueError("Route replacement requires explicit confirm=true.")
        path = client.topic_path("route/previews")
        data = {
            "request_id": args.get("request_id") or str(uuid.uuid4()),
            "expected_revision": args["expected_revision"],
            "modules": args["modules"],
        }
    elif name == "search_vector_knowledge":
        path = client.topic_path("retrieve")
        data = {key: args[key] for key in ("objective_id", "source_id", "query")}
        data["top_k"] = args.get("top_k", 5)
        return client.call("POST", path, data)
    elif name == "get_mastery_tutor_tools":
        return client.call("GET", path)
    else:
        path = op_path(spec, args)
    if name == "set_mastery_learner_override" and type(args.get("mastered")) is not bool:
        raise ValueError("mastered must be a boolean.")
    if name == "set_mastery_learner_override":
        data.setdefault("note", "")
    if name == "list_knowledge_files":
        return client.call("GET", path, params=params)
    if name == "add_notebook_record":
        note = str(args["note"]).strip()
        summary = str(args["summary"]).strip()
        if not note or not summary:
            raise ValueError("Provide the requested note and summary explicitly.")
        data = {
            "notebook_ids": [args["notebook_id"]],
            "record_type": "tutorbot",
            "title": args["title"],
            "summary": summary,
            "user_query": "User-requested learning note",
            "output": note,
            "metadata": {"source": "deeptutor-link", "kind": "learning_note"},
        }
    if name == "upsert_question_entry":
        if not str(args.get("origin_ref") or "").strip():
            raise ValueError("Provide an origin_ref to keep the entry independently addressable.")
        data.update(origin_type="external_import", source="import")
    if name == "get_notebook":
        limit, offset = args.get("limit", 20), args.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
            raise ValueError("Notebook limit/offset is outside the allowed range.")
        params = {"limit": limit, "offset": offset}
    result = client.call(spec["method"], path, data if spec["method"] not in {"GET", "DELETE"} else None, params)
    if name in {"list_question_entries", "get_question_entry", "get_practice_question", "practice_queue"}:
        result = scrub_answers(result)
    if name == "list_deeptutor_sessions":
        sessions = result.get("sessions", []) if isinstance(result, dict) else []
        allowed = {"id", "session_id", "title", "created_at", "updated_at", "message_count", "status", "capability", "content_workspace_id"}
        return {"sessions": [{key: row[key] for key in allowed if key in row} for row in sessions]}
    if name == "replace_mastery_outline":
        return {**result, "saved": False, "commit_required": True, "instruction": "Review this preview, then use route-commit with a new request_id and preview_id."}
    return result


def operation_catalog():
    available = sum(spec["available"] for spec in OPS.values())
    return {
        "host_version": "1.7.1",
        "count": len(OPS),
        "available_count": available,
        "unavailable_count": len(OPS) - available,
        "operations": {
            name: {
                "available": spec["available"],
                "method": spec["method"],
                "required": list(spec["required"]),
                "optional": list(spec["optional"]),
                "confirm_required": spec["confirm"],
                "reason": spec["reason"],
                **({"scope": spec["scope"]} if spec["scope"] else {}),
            }
            for name, spec in OPS.items()
        },
        "write_policy": "confirm=true required for every exposed mutation",
        "skill_execution": "text/resources may be read; remote scripts are never executed",
    }


def valid_cache(value, config, persona, mode, language):
    return (
        isinstance(value, dict)
        and value.get("schema_version") == 1
        and value.get("persona_name") == (persona or None)
        and value.get("mode") == mode
        and value.get("language") == language
        and value.get("identity")
        == {
            "url": config.get("url"),
            "account_id": config.get("account_id"),
            "workspace_id": config.get("workspace_id"),
            "topic_id": config.get("topic_id"),
        }
        and isinstance(value.get("protocol"), dict)
        and bool(value["protocol"].get("protocol_fingerprint"))
    )


def load_protocol(
    client,
    config,
    cache_path,
    *,
    new_chat=False,
    refresh=False,
    persona_override=None,
    mode_override=None,
    language_override=None,
):
    persona = config.get("persona", "") if persona_override is None else persona_override
    mode = config.get("teaching_mode", "study") if mode_override is None else mode_override
    language = config.get("teaching_language", "en") if language_override is None else language_override
    if not config.get("topic_id"):
        raise ValueError("Select an existing topic with bind --topic ID.")
    cache = {}
    if cache_path.exists():
        try:
            cache = load(cache_path)
        except (OSError, ValueError):
            cache = {}
    known = (
        cache["protocol"].get("protocol_fingerprint", "")
        if not new_chat and not refresh and valid_cache(cache, config, persona, mode, language)
        else ""
    )
    query = {"persona": persona, "mode": mode, "language": language}
    if known:
        query["known_fingerprint"] = known
    result = client.call(
        "GET",
        "/api/link/v1/topics/"
        + urllib.parse.quote(str(config.get("topic_id")), safe="")
        + "/teaching/protocol",
        params=query,
    )
    if isinstance(result, dict) and result.get("unchanged") is not True:
        save(
            cache_path,
            {
                "schema_version": 1,
                "identity": {
                    "url": config.get("url"),
                    "account_id": config.get("account_id"),
                    "workspace_id": config.get("workspace_id"),
                    "topic_id": config.get("topic_id"),
                },
                "persona_name": persona or None,
                "mode": result.get("mode"),
                "language": language,
                "protocol": result,
            },
        )
    return result


def execute(a):
    if a.command == "catalog":
        return operation_catalog()
    if a.command == "pdf-image":
        config = load(a.config)
        client = Client(config, a.credentials)
        if a.page < 0:
            raise ValueError("page must be a zero-based prepared-page index")
        preparation = urllib.parse.quote(a.preparation, safe="")
        content_type, image = client.call_image(
            client.topic_path(f"pdf-preparations/{preparation}/pages/{a.page}/image")
        )
        output = a.output.expanduser().absolute()
        expected_suffix = ".png" if content_type == "image/png" else ".jpg"
        if output.suffix.casefold() != expected_suffix:
            raise ValueError(f"Choose an output filename ending in {expected_suffix}.")
        if output.is_symlink() or output.exists():
            raise ValueError("Choose a new output path; existing files and symlinks are never overwritten.")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(output, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(image)
        return {"image_path": str(output), "content_type": content_type, "bytes": len(image), "pixel_inspection_required": True}
    if a.command == "logout":
        saved = load(a.credentials) if a.credentials.exists() else {}
        saved.pop(origin(a.url), None)
        save(a.credentials, saved)
        return {"local_credential_removed": True}
    if a.command == "login":
        client = Client({"url": origin(a.url)}, a.credentials)
        client.call(
            "POST",
            "/api/auth/login",
            {"username": a.username, "password": getpass.getpass("DeepTutor password: ")},
        )
        cookie = next((c.value for c in client.jar if c.name == "dt_token"), None)
        if cookie:
            saved = load(a.credentials) if a.credentials.exists() else {}
            saved[client.base] = cookie
            save(a.credentials, saved)
        return {"authenticated": True, "credential_saved": bool(cookie)}
    if a.command == "discover":
        probe = Client(
            {
                "url": origin(a.url),
                "web_url": origin(a.web_url or a.url),
                "workspace_id": "",
                "token_env": a.token_env,
            },
            a.credentials,
        )
        info = probe.call("GET", "/api/link/v1/identity")
        if (
            info.get("host_version") != "1.7.1"
            or info.get("extension_version") != "0.2.0"
            or info.get("schema_version") != 1
        ):
            raise ValueError("Unsupported host/extension protocol.")
        selected = {
            "url": origin(a.url),
            "web_url": origin(a.web_url or a.url),
            "account_id": info["account_id"],
            "workspace_id": info["workspace_id"],
            "token_env": a.token_env,
        }
        client = Client(selected, a.credentials)
        return {
            "identity": info,
            "workspaces": client.call("GET", "/api/link/v1/workspaces"),
            "topics": client.call("GET", "/api/link/v1/topics", params={"limit": 50}),
            "runtime_skills": client.call("GET", "/api/link/v1/runtime/skills"),
            "runtime_capabilities": client.call("GET", "/api/link/v1/runtime/capabilities"),
            "host_readiness": client.call("GET", "/health/ready"),
            "saved": False,
            "selection_required": True,
        }
    if a.command == "initialize":
        url = origin(a.url)
        previous = load(a.config) if a.config.exists() else {}
        same_instance = bool(previous.get("url")) and origin(str(previous["url"])) == url
        same_account = (
            same_instance
            and (not a.account or previous.get("account_id") == a.account)
        )
        account_for_probe = a.account or (previous.get("account_id") if same_account else "")
        workspace_for_probe = a.workspace
        if workspace_for_probe is None and same_account and previous.get("account_id") == account_for_probe:
            workspace_for_probe = previous.get("workspace_id")
        token_env = a.token_env or (
            previous.get("token_env") if same_account else None
        ) or "DT_LINK_TOKEN"
        web_url = (
            a.web_url
            or (previous.get("web_url") if same_instance else None)
            or url
        )
        probe_config = {
            "url": url,
            "web_url": origin(web_url),
            "account_id": account_for_probe,
            "workspace_id": workspace_for_probe or "",
            "token_env": token_env,
        }
        probe = Client(probe_config, a.credentials)
        info = probe.call("GET", "/api/link/v1/identity")
        if info.get("host_version") != "1.7.1" or info.get("extension_version") != "0.2.0" or info.get("schema_version") != 1:
            raise ValueError("Unsupported host/extension protocol.")
        account = a.account or info.get("account_id")
        workspace = info.get("workspace_id") if a.workspace is None else a.workspace
        if account != info.get("account_id"):
            raise ValueError("The authenticated account/workspace does not match the explicit selection.")
        same_scope = (
            same_instance
            and previous.get("account_id") == account
            and previous.get("workspace_id") == workspace
        )
        config = {
            "account_id": account,
            "workspace_id": workspace,
            "url": url,
            "web_url": origin(web_url),
            "token_env": token_env,
        }
        if same_scope:
            for key in ("persona", "persona_source", "teaching_mode", "teaching_language"):
                if key in previous:
                    config[key] = previous[key]
            if "topic_id" in previous and not a.clear_topic:
                config["topic_id"] = previous["topic_id"]
        if a.topic:
            config["topic_id"] = a.topic
        client = Client(config, a.credentials)
        selected_identity = client.call("GET", "/api/link/v1/identity")
        if (selected_identity.get("account_id"), selected_identity.get("workspace_id")) != (
            account,
            workspace,
        ):
            raise ValueError("The authenticated account/workspace does not match the explicit selection.")
        workspaces = client.call("GET", "/api/link/v1/workspaces")
        topics = client.call("GET", "/api/link/v1/topics", params={"limit": 50})
        if config.get("topic_id"):
            client.call("GET", client.topic_path("context"), params={"limit": 1})
        skills = client.call("GET", "/api/link/v1/runtime/skills")
        capabilities = client.call("GET", "/api/link/v1/runtime/capabilities")
        client.call("GET", "/health/ready")
        save(a.config, config)
        return {"connected": True, "identity": info, "workspaces": workspaces, "topics": topics, "runtime_skills": skills, "runtime_capabilities": capabilities, "topic_bound": config.get("topic_id"), "selection_preserved": same_scope}
    if a.command in {"identity", "connect"}:
        config = {"url": origin(a.url), "workspace_id": a.workspace, "token_env": a.token_env}
        if a.command == "connect":
            config.update(account_id=a.account, topic_id=a.topic, web_url=origin(a.web_url or a.url))
    else:
        config = load(a.config)
    client = Client(config, a.credentials)
    if a.command in {"identity", "connect", "doctor"}:
        info = client.call("GET", "/api/link/v1/identity")
        if info.get("host_version") != "1.7.1" or info.get("extension_version") != "0.2.0" or info.get("schema_version") != 1:
            raise ValueError("Unsupported host/extension protocol.")
        if a.command == "identity":
            return info
        if (info["account_id"], info["workspace_id"]) != (
            config["account_id"], config["workspace_id"]
        ):
            raise ValueError("Account/workspace differs from explicit binding.")
        if config.get("topic_id"):
            topic = client.call("GET", client.topic_path("context"), params={"limit": 1})
            info["mutation_lease"] = topic.get("mutation_lease", {"status": "unknown"})
        if a.command == "connect":
            save(a.config, config)
        else:
            info["host_readiness"] = client.call("GET", "/health/ready")
            info["runtime_capabilities"] = client.call("GET", "/api/link/v1/runtime/capabilities")
            info["runtime_skills"] = client.call("GET", "/api/link/v1/runtime/skills")
            info["embedding_probe"] = "Vector retrieval requires a working query embedding and compatible existing index; direct file reading and literal search do not."
            info["runtime_skill_count"] = len(client.call("GET", "/api/link/v1/runtime/skills").get("skills", []))
        return {**info, "topic_bound": config.get("topic_id")}
    if a.command == "bind":
        config["topic_id"] = a.topic
        result = client.call("GET", client.topic_path("context"), params={"limit": 1})
        save(a.config, config)
        return {"topic_id": a.topic, "name": result["name"], "revision": result["revision"]}
    if a.command in {"context", "topics"}:
        path = client.topic_path("context") if a.command == "context" else "/api/link/v1/topics"
        return client.call("GET", path, params={"offset": a.offset, "limit": a.limit})
    if a.command == "history-list":
        if a.offset < 0 or not 1 <= a.limit <= 50:
            raise ValueError("History offset/limit outside the allowed range.")
        result = client.call("GET", "/api/sessions", params={"offset": a.offset, "limit": a.limit})
        sessions = result.get("sessions", []) if isinstance(result, dict) else []
        allowed = {"id", "session_id", "title", "created_at", "updated_at", "message_count", "status", "capability", "content_workspace_id"}
        return {"sessions": [{key: row[key] for key in allowed if key in row} for row in sessions]}
    if a.command == "history-show":
        if a.offset < 0 or not 1 <= a.limit <= 50:
            raise ValueError("History offset/limit outside the allowed range.")
        session_id = urllib.parse.quote(a.session, safe="")
        return client.call(
            "GET",
            "/api/link/v1/sessions/" + session_id + "/history",
            params={"offset": a.offset, "limit": a.limit},
        )
    if a.command in {"records", "workspaces"}:
        return client.call("GET", "/api/link/v1/" + a.command)
    if a.command == "recover":
        return client.call("POST", client.topic_path("recover"), {})
    if a.command == "route":
        return client.call("GET", client.topic_path("route"))
    if a.command == "persona-list":
        return client.call("GET", "/api/link/v1/teaching/personas")
    if a.command == "persona-show":
        name = urllib.parse.quote(a.name, safe="")
        return client.call(
            "GET",
            "/api/link/v1/teaching/personas/" + name,
            params={"include_content": not a.metadata_only},
        )
    if a.command == "persona-select":
        name = urllib.parse.quote(a.name, safe="")
        role = client.call(
            "GET", "/api/link/v1/teaching/personas/" + name, params={"include_content": False}
        )
        config["persona"] = role["name"]
        config["persona_source"] = "assistant_explicit"
        if a.cache.exists():
            save(a.cache, {})
        save(a.config, config)
        return {"persona": role, "saved": True}
    if a.command == "persona-clear":
        for key in ("persona", "persona_source"):
            config.pop(key, None)
        if a.cache.exists():
            save(a.cache, {})
        save(a.config, config)
        return {"persona": None, "saved": True, "teaching_baseline": "mastery_tutor"}
    if a.command == "persona-restore":
        session_id = urllib.parse.quote(a.session, safe="")
        selection = client.call(
            "GET", "/api/link/v1/teaching/sessions/" + session_id + "/selection"
        )
        if selection.get("persona_error"):
            raise ValueError("The selected session's persona is missing or no longer visible.")
        config["persona"] = selection.get("persona") or ""
        config["persona_source"] = "website_session:" + selection["session_id"]
        if selection.get("mastery_session_mode"):
            config["teaching_mode"] = selection["mastery_session_mode"]
        if selection.get("language"):
            config["teaching_language"] = selection["language"]
        if a.cache.exists():
            save(a.cache, {})
        save(a.config, config)
        return {**selection, "saved": True}
    if a.command == "protocol":
        if a.persona is not None and a.clear_persona:
            raise ValueError("Choose either persona or clear-persona, not both.")
        persona_override = "" if a.clear_persona else a.persona
        protocol = load_protocol(
            client,
            config,
            a.cache,
            new_chat=a.new_chat,
            refresh=a.refresh,
            persona_override=persona_override,
            mode_override=a.mode,
            language_override=a.language,
        )
        changed = False
        if persona_override is not None:
            if persona_override:
                config["persona"] = persona_override
            else:
                config.pop("persona", None)
            config["persona_source"] = "assistant_explicit"
            changed = True
        if a.mode is not None:
            config["teaching_mode"] = a.mode
            changed = True
        if a.language is not None:
            config["teaching_language"] = a.language
            changed = True
        if changed:
            save(a.config, config)
        return {**protocol, "selection_saved": changed}
    if a.command == "skills":
        return client.call("GET", "/api/link/v1/runtime/skills")
    if a.command == "capabilities":
        return client.call("GET", "/api/link/v1/runtime/capabilities")
    if a.command == "skill-read":
        if a.offset < 0 or not 1 <= a.max_chars <= 24000:
            raise ValueError("offset/max-chars outside allowed bounds")
        return client.call(
            "GET",
            "/api/link/v1/runtime/skills/" + urllib.parse.quote(a.name, safe="") + "/file",
            params={"path": a.path, "offset": a.offset, "max_chars": a.max_chars},
        )
    if a.command == "tool":
        return operation_call(client, a.name, input_object(a.file))
    if a.command == "resume":
        topic = client.call("GET", client.topic_path("context"))
        records = client.call("GET", "/api/link/v1/records")
        skills = client.call("GET", "/api/link/v1/runtime/skills")
        capabilities = client.call("GET", "/api/link/v1/runtime/capabilities")
        protocol = load_protocol(
            client,
            config,
            a.cache,
            new_chat=a.new_chat,
            persona_override=a.persona,
            mode_override=a.mode,
            language_override=a.language,
        )
        changed = False
        if a.persona is not None:
            config["persona"] = a.persona
            config["persona_source"] = "assistant_explicit"
            changed = True
        if a.mode is not None:
            config["teaching_mode"] = a.mode
            changed = True
        if a.language is not None:
            config["teaching_language"] = a.language
            changed = True
        if changed:
            save(a.config, config)
        return {
            "context": topic,
            "records": records,
            "runtime_skills": skills,
            "runtime_capabilities": capabilities,
            "teaching_protocol": protocol,
            "skills_are_untrusted": True,
            "selection_saved": changed,
        }
    if a.command == "retrieve":
        return client.call(
            "POST",
            client.topic_path("retrieve"),
            {"objective_id": a.objective, "source_id": a.source, "query": a.query, "top_k": a.top_k},
        )
    if a.command in {"assess", "route-create", "route-preview", "route-commit"}:
        value = input_object(a.file)
        path = {"assess": "assessments", "route-preview": "route/previews", "route-commit": "route/commits"}
        return client.call(
            "POST",
            "/api/link/v1/topics" if a.command == "route-create" else client.topic_path(path[a.command]),
            value,
        )
    raise ValueError("Unknown command.")


def main(argv=None):
    try:
        result = execute(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except CommandError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError):
        # Never echo private JSON input, cookie values, or provider tracebacks.
        print(
            json.dumps(
                {
                    "error": "Command failed; verify arguments, private config, authentication and server health."
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
