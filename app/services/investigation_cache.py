"""Small, filesystem-backed cache for completed investigations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.cache_versions import (
    INVESTIGATION_CACHE_SCHEMA_VERSION,
    INVESTIGATION_PROMPT_VERSION,
    TOOL_SCHEMA_VERSION,
    WORKSPACE_POLICY_VERSION,
)

logger = logging.getLogger(__name__)


def cache_root() -> Path:
    configured = os.environ.get("TRACE_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(tempfile.gettempdir()) / "trace-cache"


def normalize_question(question: str) -> str:
    """Normalize only whitespace; preserve case and wording semantics."""
    return " ".join(question.strip().split())


@dataclass(frozen=True)
class InvestigationCacheKey:
    provider: str
    owner: str
    name: str
    revision: str
    question: str
    model: str
    prompt_version: int = INVESTIGATION_PROMPT_VERSION
    tool_schema_version: int = TOOL_SCHEMA_VERSION
    workspace_policy_version: int = WORKSPACE_POLICY_VERSION

    def payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "owner": self.owner,
            "name": self.name,
            "revision": self.revision,
            "question": self.question,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "tool_schema_version": self.tool_schema_version,
            "workspace_policy_version": self.workspace_policy_version,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class InvestigationCache:
    root: "Path"
    entries_dir: "Path"

    def __init__(self, root: Path | None = None):
        self.root = (root or cache_root()).expanduser()
        self.entries_dir = self.root / "investigations"

    def _path_for(self, key: InvestigationCacheKey) -> Path:
        return self.entries_dir / f"{key.digest}.json"

    def get(self, key: InvestigationCacheKey) -> dict[str, Any] | None:
        path = self._path_for(key)
        started = time.perf_counter()
        try:
            with path.open("r", encoding="utf-8") as f:
                value = json.load(f)
            if not isinstance(value, dict):
                return None
            if value.get("cache_schema_version") != INVESTIGATION_CACHE_SCHEMA_VERSION:
                return None
            if value.get("key") != key.payload():
                return None
            if not isinstance(value.get("investigation_result"), dict):
                return None
            if not isinstance(value.get("evidence_spans"), list):
                return None
            if value.get("termination_reason") != "model_finished":
                return None
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable investigation cache entry %s: %s", path.name, type(exc).__name__)
            return None
        finally:
            logger.debug("Investigation cache lookup took %.6fs", time.perf_counter() - started)

    def put(self, key: InvestigationCacheKey, value: dict[str, Any]) -> None:
        path = self._path_for(key)
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_expired_entries()
        payload = {
            "cache_schema_version": INVESTIGATION_CACHE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "key": key.payload(),
            **value,
        }
        fd, temp_name = tempfile.mkstemp(prefix=f".{key.digest}.", suffix=".tmp", dir=self.entries_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _cleanup_expired_entries(self) -> None:
        max_age = os.environ.get("TRACE_INVESTIGATION_CACHE_TTL_SECONDS")
        if not max_age:
            return
            
        try:
            max_age_float = float(max_age)
            now = time.time()
            for entry in self.entries_dir.iterdir():
                if entry.is_file() and entry.suffix == ".json":
                    if now - entry.stat().st_mtime > max_age_float:
                        entry.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
