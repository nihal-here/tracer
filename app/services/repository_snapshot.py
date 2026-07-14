import os
import tarfile
import tempfile
import requests
import logging
from pathlib import Path
from app.services.github import (
    GitHubRepository,
    RepositoryArchiveTooLargeError,
    RepositoryArchiveUnsafeError,
    RepositorySnapshotError,
    GITHUB_API_BASE,
    GitHubTimeoutError,
    check_github_response
)

logger = logging.getLogger(__name__)

MAX_ARCHIVE_BYTES_DOWNLOAD = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
# Limits all tar members (including directories and special files), not just regular files
MAX_ARCHIVE_MEMBERS = 10_000
MAX_INDIVIDUAL_FILE_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

class RepositorySnapshot:
    def __init__(self, gh_repo: GitHubRepository):
        self.gh_repo = gh_repo
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.root_path: Path | None = None
        self.extracted_files: frozenset[str] = frozenset()

    def __enter__(self):
        self.materialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def materialize(self):
        if self.temp_dir is not None:
            return

        self.temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(self.temp_dir.name)
        archive_path = base_path / "repo.tar.gz"
        extract_path = base_path / "extracted"
        extract_path.mkdir()

        try:
            self._do_materialize(archive_path, extract_path)
        except Exception:
            self.cleanup()
            raise

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
                        raise RepositoryArchiveUnsafeError(f"Symlinks and hardlinks are not allowed: {canonical_name}")

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

        except tarfile.TarError as e:
            raise RepositorySnapshotError(f"Failed to process archive: {e}")

        # Finalize
        self.root_path = extract_path
        self.extracted_files = frozenset(extracted_paths)

    def cleanup(self):
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
            self.root_path = None
            self.extracted_files = frozenset()
