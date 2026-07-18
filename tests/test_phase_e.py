import asyncio
import io
import json
import os
import tarfile
from types import SimpleNamespace
from typing import cast, Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.github import GitHubRepository
from app.services.investigation_cache import InvestigationCache, InvestigationCacheKey, normalize_question
from app.services.repository_snapshot import RepositorySnapshot, _snapshot_cache_key
from app.investigation_trace import InvestigationTrace
from app.investigation_trace import record_model_request_usage
from app.cache_versions import (
    INVESTIGATION_PROMPT_VERSION,
    TOOL_SCHEMA_VERSION,
    WORKSPACE_POLICY_VERSION,
)
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import RequestUsage

os.environ.setdefault("GOOGLE_API_KEY", "offline-test")


def make_key(**overrides):
    values: dict[str, Any] = {
        "provider": "github",
        "owner": "owner",
        "name": "repo",
        "revision": "sha1",
        "question": "What does it do?",
        "model": "google:gemini-3.1-flash-lite",
    }
    values.update(overrides)
    return InvestigationCacheKey(
        provider=str(values["provider"]),
        owner=str(values["owner"]),
        name=str(values["name"]),
        revision=str(values["revision"]),
        question=str(values["question"]),
        model=str(values["model"]),
        prompt_version=int(str(values.get("prompt_version", INVESTIGATION_PROMPT_VERSION))),
        tool_schema_version=int(str(values.get("tool_schema_version", TOOL_SCHEMA_VERSION))),
        workspace_policy_version=int(str(values.get("workspace_policy_version", WORKSPACE_POLICY_VERSION))),
    )


def make_cache_value(evidence=None):
    evidence = evidence or {"main.py": "print('hello')"}
    return {
        "investigation_result": {
            "summary_of_evidence": "ok",
            "delegated_interfaces_discovered": [],
            "relevant_excerpts": [{"path": "main.py", "start_line": 1, "end_line": 1, "justification": "x"}],
            "concrete_implementations_read": []
        },
        "evidence_spans": [{"path": "main.py", "start_line": 1, "end_line": 1, "content": "print('hello')", "source_action_index": 0, "truncated": False}],
        "evidence_file_paths": sorted(evidence),
        "tool_sequence": ["read_file"],
        "usage": {"model_requests": 1, "input_tokens": 10, "output_tokens": 2},
        "termination_reason": "model_finished",
    }


def test_question_normalization_is_conservative():
    assert normalize_question("  What   does it do?\n") == "What does it do?"
    assert normalize_question("Case Sensitive") != normalize_question("case sensitive")


def test_per_model_request_usage_uses_public_message_history():
    first = ModelResponse(
        parts=[ToolCallPart(tool_name="read_file", args={"file_path": "main.py"})],
        usage=RequestUsage(input_tokens=10, output_tokens=2),
    )
    second = ModelResponse(
        parts=[TextPart(content="done")],
        usage=RequestUsage(input_tokens=20, output_tokens=3, cache_read_tokens=4),
    )
    result = MagicMock()
    result.all_messages.return_value = [
        ModelRequest(parts=[]),
        first,
        ModelRequest(parts=[ToolReturnPart(tool_name="read_file", content="file content")]),
        second,
    ]
    trace = InvestigationTrace(started_at="now", question_chars=1)

    record_model_request_usage(trace, result)

    assert trace.model_request_usage[0].input_tokens == 10
    assert trace.model_request_usage[1].cumulative_input_tokens == 30
    assert trace.model_request_usage[1].cache_read_tokens == 4
    assert trace.model_request_usage[1].preceding_tool_results == ["read_file"]


def test_investigation_cache_round_trip_and_key_dimensions(tmp_path):
    cache = InvestigationCache(tmp_path)
    key = make_key(question=normalize_question("  What   does it do? "))
    cache.put(key, make_cache_value())

    cached = cache.get(key)
    assert cached is not None
    assert cached["evidence_spans"][0]["content"] == "print('hello')"
    assert cache.get(make_key(question="Different question")) is None
    assert cache.get(make_key(revision="sha2")) is None
    assert cache.get(make_key(model="google:other-model")) is None
    assert cache.get(make_key(prompt_version=2)) is None


def test_corrupt_and_incompatible_investigation_entries_are_misses(tmp_path):
    cache = InvestigationCache(tmp_path)
    key = make_key()
    path = tmp_path / "investigations" / f"{key.digest}.json"
    path.parent.mkdir()
    path.write_text("not json", encoding="utf-8")
    assert cache.get(key) is None

    path.write_text(json.dumps({"cache_schema_version": 999, "key": key.payload()}), encoding="utf-8")
    assert cache.get(key) is None

    path.write_text("[]", encoding="utf-8")
    assert cache.get(key) is None

    failed = make_cache_value()
    failed["termination_reason"] = "max_actions"
    cache.put(key, failed)
    assert cache.get(key) is None

    cache.put(key, make_cache_value())
    assert list((tmp_path / "investigations").glob("*.tmp")) == []


def test_snapshot_cache_reuses_same_sha_without_redownload(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        content = b"# README\n"
        info = tarfile.TarInfo("repo-root/README.md")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
        content = b"print('hello')\n"
        info = tarfile.TarInfo("repo-root/main.py")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    archive_bytes = archive.getvalue()

    repo = GitHubRepository("owner", "repo", "sha1", "main", {})
    response = MagicMock()
    response.headers = {"Content-Length": str(len(archive_bytes))}
    response.iter_content.return_value = [archive_bytes]
    response.__enter__.return_value = response

    with patch("app.services.repository_snapshot.requests.get", return_value=response) as get:
        first = RepositorySnapshot(repo, cache_dir=tmp_path / "cache")
        first.materialize()
        assert first.cache_hit is False
        first.cleanup()

        second = RepositorySnapshot(repo, cache_dir=tmp_path / "cache")
        second.materialize()
        assert second.cache_hit is True
        assert second.get_readme() == "# README\n"
        assert second.list_top_level_files() == ["README.md", "main.py"]
        second.cleanup()

        assert get.call_count == 1
        assert (tmp_path / "cache" / "snapshots" / _snapshot_cache_key(repo) / "complete.json").exists()


def test_snapshot_cache_corruption_is_rebuilt(tmp_path):
    repo = GitHubRepository("owner", "repo", "sha1", "main", {})
    entry = tmp_path / "cache" / "snapshots" / _snapshot_cache_key(repo)
    entry.mkdir(parents=True)
    (entry / "complete.json").write_text("{}", encoding="utf-8")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        content = b"x"
        info = tarfile.TarInfo("root/file.py")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    response = MagicMock()
    response.headers = {"Content-Length": "1"}
    response.iter_content.return_value = [archive.getvalue()]
    response.__enter__.return_value = response

    with patch("app.services.repository_snapshot.requests.get", return_value=response) as get:
        snapshot = RepositorySnapshot(repo, cache_dir=tmp_path / "cache")
        snapshot.materialize()
        assert get.call_count == 1
        assert snapshot.root_path is not None
        snapshot.cleanup()


def test_snapshot_cache_file_entry_is_rebuilt(tmp_path):
    repo = GitHubRepository("owner", "repo", "sha1", "main", {})
    entry = tmp_path / "cache" / "snapshots" / _snapshot_cache_key(repo)
    entry.parent.mkdir(parents=True)
    entry.write_text("corrupt", encoding="utf-8")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        content = b"x"
        info = tarfile.TarInfo("root/file.py")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    response = MagicMock()
    response.headers = {"Content-Length": "1"}
    response.iter_content.return_value = [archive.getvalue()]
    response.__enter__.return_value = response

    with patch("app.services.repository_snapshot.requests.get", return_value=response) as get:
        snapshot = RepositorySnapshot(repo, cache_dir=tmp_path / "cache")
        snapshot.materialize()
        assert get.call_count == 1
        assert snapshot.cache_hit is False
        assert entry.is_dir()
        snapshot.cleanup()


def test_investigation_cache_hit_skips_agent_and_keeps_current_usage_zero(tmp_path, monkeypatch):
    from app.services.answer_service import AnswerGeneratorResult
    from app.services.investigation_service import run_investigation

    monkeypatch.setenv("TRACE_CACHE_DIR", str(tmp_path / "cache"))
    repo = GitHubRepository("owner", "repo", "sha1", "main", {"language": "Python"})
    snapshot_root = tmp_path / "repo"
    snapshot_root.mkdir()
    (snapshot_root / "main.py").write_text("print('hello')", encoding="utf-8")
    snapshot = cast(RepositorySnapshot, cast(Any, SimpleNamespace(
        gh_repo=repo,
        root_path=snapshot_root,
        extracted_files=frozenset({"main.py"}),
        list_top_level_files=lambda: ["main.py"],
        get_readme=lambda: None,
    )))
    key = make_key()
    InvestigationCache(tmp_path / "cache").put(key, make_cache_value())
    trace = InvestigationTrace(started_at="now", question_chars=14)

    async def collect():
        return [event async for event in run_investigation(snapshot, "What does it do?", trace)]

    with patch("app.services.investigation_service.investigation_agent.run", new=AsyncMock(side_effect=AssertionError("agent ran"))), \
         patch("app.services.investigation_service.prepare_answer_stream", return_value=AnswerGeneratorResult(0, iter(["answer"]))):
        print('CACHE CONTENTS:'); import os; os.system(f'cat {tmp_path}/cache/investigations/*.json'); print('CACHE CONTENTS:'); import os; os.system(f'cat {tmp_path}/cache/investigations/*.json'); events = asyncio.run(collect())

    assert events[-1].__class__.__name__ == "InvestigationCompleted"
    assert trace.investigation_cache_hit is True
    assert trace.model_requests == 0
    assert trace.input_tokens == 0
    assert trace.output_tokens == 0
    assert trace.evidence_file_paths == ["main.py"]


def test_eval_runner_is_explicit_and_dispatches_without_running_live_calls():
    import evals.runner as runner

    assert runner.main([]) == 0
    assert runner.main(["--all"]) == 2
    assert runner.main(["--case", "missing-case"]) == 2

    with patch.object(runner, "run_cases") as run_cases, patch.object(runner.asyncio, "run") as asyncio_run:
        assert runner.main(["--case", "requests-session-002"]) == 0
        run_cases.assert_called_once_with([runner._case_by_id("requests-session-002")])
        asyncio_run.assert_called_once()

    with patch.object(runner, "run_cases") as run_cases, patch.object(runner.asyncio, "run"):
        assert runner.main(["--all", "--confirm-live"]) == 0
        run_cases.assert_called_once_with(runner.ALL_CASES)


def test_diagnostics_is_explicit_and_rejects_unconfirmed_suite():
    import evals.run_diagnostics as diagnostics

    assert diagnostics.main([]) == 0
    assert diagnostics.main(["--all"]) == 2
    assert diagnostics.main(["--case", "missing-case"]) == 2


def test_importing_runner_does_not_dispatch_evaluations():
    import importlib
    import evals.runner as runner

    with patch.object(runner, "run_cases") as run_cases:
        importlib.reload(runner)
        run_cases.assert_not_called()
