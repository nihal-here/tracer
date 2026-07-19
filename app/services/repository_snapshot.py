import os
import json
import hashlib
import shutil
import tarfile
import tempfile
import requests
import logging
import threading
import time
from pathlib import Path
from typing import Any, cast
from app.services.github import (
    GitHubRepository,
    RepositoryArchiveTooLargeError,
    RepositoryArchiveUnsafeError,
    RepositorySnapshotError,
    GITHUB_API_BASE,
    GitHubTimeoutError,
    check_github_response
)
from app.cache_versions import SNAPSHOT_CACHE_SCHEMA_VERSION
from app.services.investigation_cache import cache_root

logger = logging.getLogger(__name__)

MAX_ARCHIVE_BYTES_DOWNLOAD = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
# Limits all tar members (including directories and special files), not just regular files
MAX_ARCHIVE_MEMBERS = 10_000
MAX_INDIVIDUAL_FILE_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


def _snapshot_cache_key(repo: GitHubRepository) -> str:
    raw = f"github\0{repo.owner}\0{repo.name}\0{repo.revision}".encode()
    return hashlib.sha256(raw).hexdigest()


def _snapshot_lock(key: str) -> threading.Lock:
    with _CACHE_LOCKS_GUARD:
        return _CACHE_LOCKS.setdefault(key, threading.Lock())


def _remove_cache_entry(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

class RepositorySnapshot:
    def __init__(self, gh_repo: GitHubRepository, cache_dir: Path | None = None):
        self.gh_repo = gh_repo
        self.cache_dir = cache_dir.expanduser() if cache_dir is not None else None
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.root_path: Path | None = None
        self.extracted_files: frozenset[str] = frozenset()
        self.is_cached = False
        self.cache_hit = False
        self.cache_lookup_duration_sec = 0.0
        self._cache_entry: Path | None = None

    def __enter__(self):
        self.materialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def materialize(self):
        if self.root_path is not None:
            return

        root = (self.cache_dir or cache_root()) / "snapshots"
        key = _snapshot_cache_key(self.gh_repo)
        final_entry = root / key
        started = time.perf_counter()

        try:
            root.mkdir(parents=True, exist_ok=True)
            self._cleanup_expired_snapshots(root)
            with _snapshot_lock(key):
                cached_files = self._load_cached_entry(final_entry)
                if cached_files is not None:
                    self.root_path = final_entry / "extracted"
                    self.extracted_files = frozenset(cached_files)
                    self.is_cached = True
                    self.cache_hit = True
                    self._cache_entry = final_entry
                    return

                if final_entry.exists():
                    _remove_cache_entry(final_entry)

                population = Path(tempfile.mkdtemp(prefix=f".{key}.", dir=root))
                extract_path = population / "extracted"
                extract_path.mkdir()
                try:
                    self._do_materialize(population / "repo.tar.gz", extract_path)
                    manifest = {
                        "cache_schema_version": SNAPSHOT_CACHE_SCHEMA_VERSION,
                        "created_at": time.time(),
                        "owner": self.gh_repo.owner,
                        "name": self.gh_repo.name,
                        "revision": self.gh_repo.revision,
                        "extracted_files": sorted(self.extracted_files),
                    }
                    self._write_manifest(population / "complete.json", manifest)
                    os.replace(population, final_entry)
                    self.root_path = final_entry / "extracted"
                    self.extracted_files = frozenset(cast(list[str], manifest["extracted_files"]))
                    self.is_cached = True
                    self.cache_hit = False
                    self._cache_entry = final_entry
                except Exception:
                    shutil.rmtree(population, ignore_errors=True)
                    raise
        except OSError as exc:
            logger.warning("Repository snapshot cache unavailable; using temporary materialization: %s", type(exc).__name__)
            self._materialize_temporary()
        finally:
            self.cache_lookup_duration_sec = time.perf_counter() - started
            logger.debug("Repository snapshot cache lookup/materialization took %.6fs", self.cache_lookup_duration_sec)

    def _cleanup_expired_snapshots(self, root: Path) -> None:
        max_age = os.environ.get("TRACE_SNAPSHOT_CACHE_TTL_SECONDS")
        if not max_age:
            return
        
        try:
            max_age_float = float(max_age)
            now = time.time()
            for entry in root.iterdir():
                if entry.is_dir() and entry.name != "extracted":
                    if now - entry.stat().st_mtime > max_age_float:
                        shutil.rmtree(entry, ignore_errors=True)
        except (ValueError, OSError):
            pass

    def _materialize_temporary(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(self.temp_dir.name)
        extract_path = base_path / "extracted"
        extract_path.mkdir()
        try:
            self._do_materialize(base_path / "repo.tar.gz", extract_path)
            self.is_cached = False
            self.cache_hit = False
        except Exception:
            self.cleanup()
            raise

    def _load_cached_entry(self, entry: Path) -> list[str] | None:
        manifest_path = entry / "complete.json"
        extracted_path = entry / "extracted"
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                raw_manifest = json.load(f)
            if not isinstance(raw_manifest, dict):
                return None
            manifest: dict[str, Any] = raw_manifest
            max_age = os.environ.get("TRACE_SNAPSHOT_CACHE_TTL_SECONDS")
            if max_age:
                age = time.time() - float(manifest["created_at"])
                if age > float(max_age):
                    return None
            if manifest.get("cache_schema_version") != SNAPSHOT_CACHE_SCHEMA_VERSION:
                return None
            if manifest.get("owner") != self.gh_repo.owner:
                return None
            if manifest.get("name") != self.gh_repo.name:
                return None
            if manifest.get("revision") != self.gh_repo.revision:
                return None
            files = manifest.get("extracted_files")
            if not isinstance(files, list) or not extracted_path.is_dir():
                return None
            for path in files:
                if not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts:
                    return None
                if not (extracted_path / path).resolve().is_relative_to(extracted_path.resolve()):
                    return None
                if not (extracted_path / path).is_file():
                    return None
            return files
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_manifest(path: Path, manifest: dict[str, Any]):
        with path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())

    def _do_materialize(self, archive_path: Path, extract_path: Path):
        url = f"{GITHUB_API_BASE}/{self.gh_repo.owner}/{self.gh_repo.name}/tarball/{self.gh_repo.revision}"
        headers = {}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            with requests.get(url, headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()

                content_length = r.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_ARCHIVE_BYTES_DOWNLOAD:
                    raise RepositoryArchiveTooLargeError(f"Archive exceeds {MAX_ARCHIVE_BYTES_DOWNLOAD} bytes (declared: {content_length})")

                downloaded_bytes = 0
                with open(archive_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > MAX_ARCHIVE_BYTES_DOWNLOAD:
                            raise RepositoryArchiveTooLargeError(f"Archive stream exceeds limit of {MAX_ARCHIVE_BYTES_DOWNLOAD} bytes")
                        f.write(chunk)

        except requests.exceptions.Timeout as e:
            raise GitHubTimeoutError(f"Timeout downloading archive: {e}")
        except requests.exceptions.RequestException as e:
            if e.response is not None:
                check_github_response(e.response, endpoint=url)
            raise RepositorySnapshotError(f"Failed to download archive: {e}")

        # Extraction
        extracted_paths = []
        try:
            with tarfile.open(archive_path, mode="r:gz") as tar:
                members_count = 0
                total_extracted_bytes = 0
                common_prefix = None
                skipped_symlinks_count = 0

                for tarinfo in tar:
                    members_count += 1
                    if members_count > MAX_ARCHIVE_MEMBERS:
                        raise RepositoryArchiveTooLargeError(f"Archive has too many members (> {MAX_ARCHIVE_MEMBERS})")

                    # Validate path traversal
                    name = tarinfo.name
                    if name.startswith("/") or ".." in name.split("/"):
                        raise RepositoryArchiveUnsafeError(f"Unsafe path in archive: {name}")

                    # Root prefix detection and validation
                    parts = name.split("/")
                    if not parts:
                        continue

                    member_prefix = parts[0]
                    if common_prefix is None:
                        common_prefix = member_prefix
                    elif common_prefix != member_prefix:
                        raise RepositoryArchiveUnsafeError("Archive contains multiple distinct root directories")

                    canonical_name = "/".join(parts[1:])
                    if not canonical_name:
                        continue

                    if tarinfo.issym() or tarinfo.islnk():
                        skipped_symlinks_count += 1
                        continue

                    if not tarinfo.isreg():
                        continue

                    if tarinfo.size > MAX_INDIVIDUAL_FILE_BYTES:
                        raise RepositoryArchiveTooLargeError(f"File {tarinfo.name} exceeds {MAX_INDIVIDUAL_FILE_BYTES} bytes")

                    target_path = extract_path / canonical_name
                    # Secure containment validation
                    if not target_path.resolve().is_relative_to(extract_path.resolve()):
                        raise RepositoryArchiveUnsafeError(f"Path traversal detected: {canonical_name}")

                    # Ensure parent dirs exist
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # Extract file in chunks
                    f_in = tar.extractfile(tarinfo)
                    if f_in is None:
                        continue

                    with open(target_path, "wb") as f_out:
                        file_bytes = 0
                        while True:
                            chunk = f_in.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            file_bytes += len(chunk)
                            total_extracted_bytes += len(chunk)

                            if file_bytes > MAX_INDIVIDUAL_FILE_BYTES:
                                raise RepositoryArchiveTooLargeError(f"File {canonical_name} exceeds {MAX_INDIVIDUAL_FILE_BYTES} bytes")
                            if total_extracted_bytes > MAX_EXTRACTED_BYTES:
                                raise RepositoryArchiveTooLargeError(f"Extracted archive exceeds {MAX_EXTRACTED_BYTES} bytes")

                            f_out.write(chunk)

                    extracted_paths.append(canonical_name)

                if skipped_symlinks_count > 0:
                    logger.warning(f"Skipped {skipped_symlinks_count} symlink/hardlink entries during repository extraction")

        except tarfile.TarError as e:
            raise RepositorySnapshotError(f"Failed to process archive: {e}")

        # Finalize
        self.root_path = extract_path
        self.extracted_files = frozenset(extracted_paths)

    def cleanup(self):
        if self.is_cached:
            self.root_path = None
            self.extracted_files = frozenset()
            self._cache_entry = None
            self.is_cached = False
            self.cache_hit = False
            return
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
            self.root_path = None
            self.extracted_files = frozenset()
            self.cache_hit = False

    def list_top_level_files(self) -> list[str]:
        """Return the names visible at the repository root from local files."""
        names = {path.split("/", 1)[0] for path in self.extracted_files if path}
        return sorted(names)

    def get_readme(self) -> str | None:
        """Read a conventional README from the materialized snapshot."""
        if self.root_path is None:
            return None
        candidates = [
            path for path in self.extracted_files
            if "/" not in path and path.lower().startswith("readme")
        ]
        candidates.sort(key=lambda path: (path.lower() != "readme.md", path.lower()))
        for relative_path in candidates:
            try:
                return (self.root_path / relative_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return None
