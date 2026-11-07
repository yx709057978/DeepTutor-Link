import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deeptutor_link.installer import setup_skill


@pytest.fixture
def standalone(tmp_path):
    setup_skill(tmp_path / "agents/skills")
    script = tmp_path / "agents/skills/deeptutor-link/scripts/deeptutor_link.py"
    guide = script.parents[1] / "references/setup.md"
    assert guide.is_file()
    workflows = script.parents[1] / "references/workflows.md"
    assert workflows.is_file()
    assert "pdf-image" in workflows.read_text()
    assert "references/setup.md" in (script.parents[1] / "SKILL.md").read_text()
    config, credentials = tmp_path / "config.json", tmp_path / "credentials.json"
    cache = tmp_path / "teaching-protocol.json"

    def run(*args, stdin=None):
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "DT_LINK_TOKEN"}}
        env["DT_LINK_SKILL_TEST_TOKEN"] = "synthetic-only-token"
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(script),
                "--config",
                str(config),
                "--credentials",
                str(credentials),
                "--cache",
                str(cache),
                *args,
            ],
            input=stdin,
            text=True,
            capture_output=True,
            cwd=tmp_path,
            env=env,
            timeout=15,
        )

    return run, config, credentials


def test_distributed_skill_runs_without_source_or_global_cli(standalone):
    run, config, _ = standalone
    result = run("--help")
    assert result.returncode == 0, result.stderr
    assert "stdlib only" in result.stdout
    assert not config.exists()


def test_distributed_skill_connects_and_continues_using_http(standalone):
    run, config, credentials = standalone
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            if self.headers.get("Authorization") != "Bearer synthetic-only-token":
                self.send_response(401)
                self.end_headers()
                return
            if self.path.startswith("/api/link/v1/identity"):
                value = {
                    "schema_version": 1,
                    "host_version": "1.7.1",
                    "extension_version": "0.2.0",
                    "account_id": "existing-user",
                    "workspace_id": "existing-workspace",
                }
            elif "/context" in self.path:
                value = {
                    "topic_id": "existing-topic",
                    "name": "Original route",
                    "revision": 7,
                    "objectives": [{"id": "original-objective"}],
                    "web_url": "/learning/mastery/existing-topic?dt_workspace=existing-workspace",
                }
            elif self.path == "/health/ready":
                value = {"status": "ready"}
            else:
                value = {"topics": [{"topic_id": "existing-topic"}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        connected = run(
            "connect",
            "--url",
            url,
            "--web-url",
            "https://original-web.example",
            "--account",
            "existing-user",
            "--workspace",
            "existing-workspace",
            "--token-env",
            "DT_LINK_SKILL_TEST_TOKEN",
        )
        assert connected.returncode == 0, connected.stderr
        assert run("topics").returncode == 0
        assert run("bind", "--topic", "existing-topic").returncode == 0
        result = run("context")
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["objectives"][0]["id"] == "original-objective"
        assert value["web_url"].startswith("https://original-web.example/learning/mastery/")
        assert run("doctor").returncode == 0
        assert "synthetic-only-token" not in config.read_text()
        assert not credentials.exists()
        assert all(h.get("X-Deeptutor-Workspace") == "existing-workspace" for _, h in calls)
        assert all(h.get("X-Deeptutor-Account") == "existing-user" for _, h in calls)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_distributed_skill_route_commands_use_bound_scope_and_stdin(standalone):
    from deeptutor_link.assets.skill.scripts.deeptutor_link import save

    run, config, _ = standalone
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(None)

        def do_POST(self):
            self.respond(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

        def respond(self, body):
            calls.append((self.command, self.path, body, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"saved": self.command == "POST"}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        save(
            config,
            {
                "url": f"http://127.0.0.1:{server.server_port}",
                "account_id": "original-user",
                "workspace_id": "original-workspace",
                "topic_id": "original/topic",
                "token_env": "DT_LINK_SKILL_TEST_TOKEN",
            },
        )
        assert run("route").returncode == 0
        payload = {"name": "Private route name", "request_id": "synthetic-id"}
        for command in ("route-create", "route-preview", "route-commit"):
            result = run(command, "--file", "-", stdin=json.dumps(payload))
            assert result.returncode == 0, result.stderr
            assert "Private route name" not in result.stdout
        assert [(method, path) for method, path, _, _ in calls] == [
            ("GET", "/api/link/v1/topics/original%2Ftopic/route"),
            ("POST", "/api/link/v1/topics"),
            ("POST", "/api/link/v1/topics/original%2Ftopic/route/previews"),
            ("POST", "/api/link/v1/topics/original%2Ftopic/route/commits"),
        ]
        assert all(body == payload for _, _, body, _ in calls[1:])
        assert all(h["X-Deeptutor-Workspace"] == "original-workspace" for *_, h in calls)
        assert all(h["X-Deeptutor-Account"] == "original-user" for *_, h in calls)
        save(config, {k: v for k, v in json.loads(config.read_text()).items() if k != "topic_id"})
        # Creation requires account/workspace but does not require or replace a topic binding.
        assert run("route-create", "--file", "-", stdin="{}").returncode == 0
        assert "topic_id" not in json.loads(config.read_text())
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_initialize_resume_and_remote_skill_reads_are_scoped_and_bounded(standalone):
    run, config, credentials = standalone
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit

            parsed = urlsplit(self.path)
            calls.append((parsed.path, parse_qs(parsed.query), dict(self.headers)))
            if self.headers.get("Authorization") != "Bearer synthetic-only-token":
                self.send_response(401)
                self.end_headers()
                return
            if parsed.path == "/api/link/v1/identity":
                value = {
                    "schema_version": 1,
                    "host_version": "1.7.1",
                    "extension_version": "0.2.0",
                    "account_id": "account-a",
                    "workspace_id": "workspace-a",
                }
            elif parsed.path == "/api/link/v1/workspaces":
                value = {"workspaces": [{"id": "workspace-a"}]}
            elif parsed.path == "/api/link/v1/topics":
                value = {"topics": [{"path_id": "topic-a"}]}
            elif parsed.path.endswith("/context"):
                value = {
                    "topic_id": "topic-a",
                    "name": "Existing topic",
                    "revision": 4,
                    "objectives": [{"id": "objective-a"}],
                }
            elif parsed.path == "/api/link/v1/records":
                value = {"reading": [], "journal": {"insights": ["untrusted"]}}
            elif parsed.path == "/api/link/v1/runtime/skills":
                value = {"skills": [{"name": "reference-skill"}]}
            elif parsed.path == "/api/link/v1/runtime/capabilities":
                value = {
                    "host_models": {
                        "chat": {"configured": False, "model": ""},
                        "embedding": {"configured": False, "model": ""},
                    },
                    "assistant_owned": {"requires_host_chat_model": False},
                    "retrieval": {
                        "direct_read_and_literal_search": True,
                        "existing_index_alone_is_sufficient": False,
                    },
                }
            elif parsed.path == "/api/link/v1/runtime/skills/reference-skill/file":
                assert parse_qs(parsed.query) == {
                    "path": ["SKILL.md"],
                    "offset": ["3"],
                    "max_chars": ["5"],
                }
                value = {
                    "name": "reference-skill",
                    "content": "trusted?",
                    "next_offset": 8,
                    "untrusted_content": True,
                }
            elif parsed.path == "/api/link/v1/topics/topic-a/teaching/protocol":
                assert "known_fingerprint" not in parse_qs(parsed.query)
                value = {
                    "schema_version": 1,
                    "unchanged": False,
                    "persona_name": None,
                    "mode": "study",
                    "protocol_fingerprint": "sha256:synthetic-protocol",
                    "sections": [{"id": "general", "content": "untrusted"}],
                    "untrusted_task_guidance": True,
                }
            elif parsed.path == "/health/ready":
                value = {"status": "ready"}
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        discovery = run(
            "discover",
            "--url",
            url,
            "--web-url",
            "https://original.example",
            "--token-env",
            "DT_LINK_SKILL_TEST_TOKEN",
        )
        assert discovery.returncode == 0, discovery.stderr
        found = json.loads(discovery.stdout)
        assert found["saved"] is False
        assert found["identity"]["account_id"] == "account-a"
        assert found["identity"]["workspace_id"] == "workspace-a"
        assert found["workspaces"]["workspaces"][0]["id"] == "workspace-a"
        assert found["runtime_capabilities"]["host_models"]["chat"]["configured"] is False
        assert found["runtime_capabilities"]["retrieval"]["direct_read_and_literal_search"] is True
        initialized = run(
            "initialize",
            "--url",
            url,
            "--web-url",
            "https://original.example",
            "--topic",
            "topic-a",
            "--token-env",
            "DT_LINK_SKILL_TEST_TOKEN",
        )
        assert initialized.returncode == 0, initialized.stderr
        assert json.loads(initialized.stdout)["topic_bound"] == "topic-a"
        resume = run("resume")
        assert resume.returncode == 0, resume.stderr
        resumed = json.loads(resume.stdout)
        assert resumed["context"]["objectives"][0]["id"] == "objective-a"
        assert resumed["runtime_skills"]["skills"][0]["name"] == "reference-skill"
        assert resumed["runtime_capabilities"] == found["runtime_capabilities"]
        assert resumed["teaching_protocol"]["sections"][0]["content"] == "untrusted"
        assert resumed["teaching_protocol"]["untrusted_task_guidance"] is True
        assert resumed["skills_are_untrusted"] is True
        skill_read = run("skill-read", "reference-skill", "--offset", "3", "--max-chars", "5")
        assert skill_read.returncode == 0, skill_read.stderr
        assert json.loads(skill_read.stdout)["untrusted_content"] is True
        assert not credentials.exists()
        assert "synthetic-only-token" not in config.read_text()
        scoped = [h for _, _, h in calls if h.get("X-Deeptutor-Account")]
        assert scoped
        assert all(h.get("X-Deeptutor-Account") == "account-a" for h in scoped)
        assert all(h.get("X-Deeptutor-Workspace") == "workspace-a" for h in scoped)
        assert any(
            path == "/api/link/v1/identity" and not h.get("X-Deeptutor-Account")
            for path, _, h in calls
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
