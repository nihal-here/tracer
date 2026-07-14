import pytest
import base64
import unittest
from unittest.mock import patch, MagicMock
from app.services.github import GitHubRepository, InvalidGitHubURLError, GitHubAPIError

class TestGitHubRepository(unittest.TestCase):
    @patch('app.services.github.requests.get')
    def test_repository_pinning_invariant(self, mock_get):
        # Setup mock responses
        def side_effect(url, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200

            if url == "https://api.github.com/repos/testowner/testrepo":
                mock_response.json.return_value = {"default_branch": "main", "description": "test"}
            elif url == "https://api.github.com/repos/testowner/testrepo/branches/main":
                mock_response.json.return_value = {"commit": {"sha": "abc123pinnedsha"}}
            elif url == "https://api.github.com/repos/testowner/testrepo/git/trees/abc123pinnedsha?recursive=1":
                mock_response.json.return_value = {"tree": [{"path": "file1.py", "type": "blob"}]}
            elif url == "https://api.github.com/repos/testowner/testrepo/readme?ref=abc123pinnedsha":
                mock_response.json.return_value = {"content": base64.b64encode(b"hello readme").decode("utf-8")}
            elif url == "https://api.github.com/repos/testowner/testrepo/contents/file1.py?ref=abc123pinnedsha":
                mock_response.json.return_value = {"content": base64.b64encode(b"print('hello')").decode("utf-8")}
            else:
                mock_response.status_code = 404
                mock_response.json.return_value = {"message": "Not Found"}

            return mock_response

        mock_get.side_effect = side_effect

        # 1. Resolve Repo
        repo = GitHubRepository.from_url("https://github.com/testowner/testrepo")
        self.assertEqual(repo.owner, "testowner")
        self.assertEqual(repo.name, "testrepo")
        self.assertEqual(repo.revision, "abc123pinnedsha")

        # 2. Verify List Files uses pinned SHA
        files = repo.list_files()
        self.assertEqual(files, ["file1.py"])

        # 3. Verify Readme uses pinned SHA
        readme = repo.get_readme()
        self.assertEqual(readme, "hello readme")

        urls_called = [call[0][0] for call in mock_get.call_args_list]
        self.assertEqual(urls_called.count("https://api.github.com/repos/testowner/testrepo"), 1)
        self.assertEqual(urls_called.count("https://api.github.com/repos/testowner/testrepo/branches/main"), 1)
        self.assertIn("https://api.github.com/repos/testowner/testrepo/git/trees/abc123pinnedsha?recursive=1", urls_called)
        self.assertIn("https://api.github.com/repos/testowner/testrepo/readme?ref=abc123pinnedsha", urls_called)

    def test_invalid_url(self):
        with self.assertRaises(InvalidGitHubURLError):
            GitHubRepository.from_url("https://gitlab.com/test/repo")

        with self.assertRaises(InvalidGitHubURLError):
            GitHubRepository.from_url("https://github.com/testowner")

        # Extra path segments are rejected
        with self.assertRaises(InvalidGitHubURLError):
            GitHubRepository.from_url("https://github.com/testowner/testrepo/issues/123")

        with self.assertRaises(InvalidGitHubURLError):
            GitHubRepository.from_url("https://github.com/testowner/testrepo/tree/main")

        # Empty/repeated path components are rejected
        with self.assertRaises(InvalidGitHubURLError):
            GitHubRepository.from_url("https://github.com//testowner/testrepo")

        with self.assertRaises(InvalidGitHubURLError):
            GitHubRepository.from_url("https://github.com/testowner//testrepo")

    @patch('app.services.github.requests.get')
    def test_missing_default_branch(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Missing default_branch
        mock_response.json.return_value = {"description": "test"}
        mock_get.return_value = mock_response

        with self.assertRaisesRegex(GitHubAPIError, "missing a valid 'default_branch'"):
            GitHubRepository.from_url("https://github.com/testowner/testrepo")

    @patch('app.services.github.requests.get')
    def test_malformed_branch_resolution(self, mock_get):
        def side_effect(url, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200

            if url == "https://api.github.com/repos/testowner/testrepo":
                mock_response.json.return_value = {"default_branch": "main"}
            elif url == "https://api.github.com/repos/testowner/testrepo/branches/main":
                # Malformed response, no commit sha
                mock_response.json.return_value = {"name": "main"}

            return mock_response

        mock_get.side_effect = side_effect

        with self.assertRaisesRegex(GitHubAPIError, "Failed to resolve branch main"):
            GitHubRepository.from_url("https://github.com/testowner/testrepo")

    @patch('app.services.github.requests.get')
    def test_malformed_branch_commit_none(self, mock_get):
        def side_effect(url, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            if url == "https://api.github.com/repos/testowner/testrepo":
                mock_response.json.return_value = {"default_branch": "main"}
            else:
                mock_response.json.return_value = {"commit": None}
            return mock_response
        mock_get.side_effect = side_effect

        with self.assertRaisesRegex(GitHubAPIError, "Failed to resolve branch main"):
            GitHubRepository.from_url("https://github.com/testowner/testrepo")

    @patch('app.services.github.requests.get')
    def test_malformed_branch_empty_sha(self, mock_get):
        def side_effect(url, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200
            if url == "https://api.github.com/repos/testowner/testrepo":
                mock_response.json.return_value = {"default_branch": "main"}
            else:
                mock_response.json.return_value = {"commit": {"sha": ""}}
            return mock_response
        mock_get.side_effect = side_effect

        with self.assertRaisesRegex(GitHubAPIError, "Failed to resolve branch main"):
            GitHubRepository.from_url("https://github.com/testowner/testrepo")


    @patch("app.services.repository_snapshot.requests.get")
    def test_snapshot_cleanup_on_failure(self, mock_get):
        from app.services.repository_snapshot import RepositorySnapshot, RepositoryArchiveTooLargeError
        repo = GitHubRepository(owner="testowner", name="testrepo", revision="abc", default_branch="main", metadata={})
        snapshot = RepositorySnapshot(repo)

        mock_response = MagicMock()
        mock_response.headers = {"Content-Length": "9999999999"}
        mock_response.status_code = 200
        mock_get.return_value.__enter__.return_value = mock_response

        with self.assertRaises(RepositoryArchiveTooLargeError):
            snapshot.materialize()

        self.assertIsNone(snapshot.temp_dir)
        self.assertIsNone(snapshot.root_path)
        self.assertEqual(snapshot.extracted_files, frozenset())

        mock_response2 = MagicMock()
        mock_response2.headers = {"Content-Length": "100"}
        mock_response2.status_code = 200
        mock_response2.iter_content.return_value = []

        with patch("app.services.repository_snapshot.tarfile.open") as mock_tar:
            mock_tar.return_value.__enter__.return_value = []
            mock_get.return_value.__enter__.return_value = mock_response2
            snapshot.materialize()

            self.assertIsNotNone(snapshot.temp_dir)
            self.assertIsNotNone(snapshot.root_path)

def test_github_403_rate_limit_classification():
    from app.services.github import check_github_response, GitHubRateLimitError, GitHubAPIError
    import requests

    # 403 with headers
    r = requests.Response()
    r.status_code = 403
    r.headers["X-RateLimit-Remaining"] = "0"

    with pytest.raises(GitHubRateLimitError):
        check_github_response(r, "endpoint")

    # 403 with body string
    r2 = requests.Response()
    r2.status_code = 403
    r2._content = b"rate limit exceeded"

    with pytest.raises(GitHubRateLimitError):
        check_github_response(r2, "endpoint")

    # generic 403
    r3 = requests.Response()
    r3.status_code = 403
    r3._content = b"generic forbidden"

    with pytest.raises(GitHubAPIError, match="forbidden"):
        check_github_response(r3, "endpoint")


def test_malicious_archive_rejection():
    from app.services.repository_snapshot import RepositorySnapshot
    from app.services.github import GitHubRepository, RepositoryArchiveUnsafeError
    import tarfile
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = Path(tmp_dir) / "bad.tar.gz"

        with tarfile.open(archive_path, "w:gz") as tar:
            # Traversal
            t1 = tarfile.TarInfo("root/../bad.txt")
            t1.type = tarfile.REGTYPE
            tar.addfile(t1, None)

        repo = GitHubRepository("owner", "repo", "sha", "main", {})
        snap = RepositorySnapshot(repo)

        with patch("app.services.repository_snapshot.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Length": "100"}
            mock_resp.iter_content.return_value = [archive_path.read_bytes()]
            mock_get.return_value.__enter__.return_value = mock_resp

            with pytest.raises(RepositoryArchiveUnsafeError, match="Unsafe path in archive"):
                snap._do_materialize(archive_path, Path(tmp_dir) / "ext")

        # Now test symlink
        with tarfile.open(archive_path, "w:gz") as tar:
            t1 = tarfile.TarInfo("root/link")
            t1.type = tarfile.SYMTYPE
            t1.linkname = "/etc/passwd"
            tar.addfile(t1, None)

        with patch("app.services.repository_snapshot.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Length": "100"}
            mock_resp.iter_content.return_value = [archive_path.read_bytes()]
            mock_get.return_value.__enter__.return_value = mock_resp

            with pytest.raises(RepositoryArchiveUnsafeError, match="Symlinks and hardlinks are not allowed"):
                snap._do_materialize(archive_path, Path(tmp_dir) / "ext")


def test_malicious_archive_budgets():
    from app.services.repository_snapshot import RepositorySnapshot
    from app.services.github import GitHubRepository, RepositoryArchiveUnsafeError, RepositoryArchiveTooLargeError
    import tarfile
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    import app.services.repository_snapshot as snap_module

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = Path(tmp_dir) / "bad.tar.gz"
        repo = GitHubRepository("owner", "repo", "sha", "main", {})
        snap = RepositorySnapshot(repo)

        # 1. Absolute path
        import io
        with tarfile.open(archive_path, "w:gz") as tar:
            t1 = tarfile.TarInfo("/etc/passwd")
            t1.type = tarfile.REGTYPE
            t1.size = 1
            tar.addfile(t1, io.BytesIO(b"x"))
        with patch("app.services.repository_snapshot.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Length": "10"}
            mock_resp.iter_content.return_value = [archive_path.read_bytes()]
            mock_get.return_value.__enter__.return_value = mock_resp
            with pytest.raises(RepositoryArchiveUnsafeError, match="Unsafe path in archive"):
                snap._do_materialize(archive_path, Path(tmp_dir) / "ext1")

        # 2. Hardlink
        with tarfile.open(archive_path, "w:gz") as tar:
            t1 = tarfile.TarInfo("root/link")
            t1.type = tarfile.LNKTYPE
            t1.linkname = "root/other"
            t1.size = 1
            tar.addfile(t1, io.BytesIO(b"x"))
        with patch("app.services.repository_snapshot.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Length": "10"}
            mock_resp.iter_content.return_value = [archive_path.read_bytes()]
            mock_get.return_value.__enter__.return_value = mock_resp
            with pytest.raises(RepositoryArchiveUnsafeError, match="Symlinks and hardlinks are not allowed"):
                snap._do_materialize(archive_path, Path(tmp_dir) / "ext2")

        # 3. Inconsistent top-level prefixes
        with tarfile.open(archive_path, "w:gz") as tar:
            t1 = tarfile.TarInfo("root1/file1")
            t1.type = tarfile.REGTYPE
            t1.size = 1
            tar.addfile(t1, io.BytesIO(b"x"))
            t2 = tarfile.TarInfo("root2/file2")
            t2.type = tarfile.REGTYPE
            t2.size = 1
            tar.addfile(t2, io.BytesIO(b"x"))
        with patch("app.services.repository_snapshot.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Length": "10"}
            mock_resp.iter_content.return_value = [archive_path.read_bytes()]
            mock_get.return_value.__enter__.return_value = mock_resp
            with pytest.raises(RepositoryArchiveUnsafeError, match="Archive contains multiple distinct root directories"):
                snap._do_materialize(archive_path, Path(tmp_dir) / "ext3")

        # 4. Individual file size too large
        with patch.object(snap_module, "MAX_INDIVIDUAL_FILE_BYTES", 5):
            with tarfile.open(archive_path, "w:gz") as tar:
                t1 = tarfile.TarInfo("root/file1")
                t1.type = tarfile.REGTYPE
                t1.size = 10
                tar.addfile(t1, io.BytesIO(b"1234567890"))
            with patch("app.services.repository_snapshot.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.headers = {"Content-Length": "10"}
                mock_resp.iter_content.return_value = [archive_path.read_bytes()]
                mock_get.return_value.__enter__.return_value = mock_resp
                with pytest.raises(RepositoryArchiveTooLargeError, match="exceeds 5 bytes"):
                    snap._do_materialize(archive_path, Path(tmp_dir) / "ext4")

        # 5. Cumulative extracted bytes too large
        with patch.object(snap_module, "MAX_EXTRACTED_BYTES", 15):
            with tarfile.open(archive_path, "w:gz") as tar:
                t1 = tarfile.TarInfo("root/file1")
                t1.type = tarfile.REGTYPE
                t1.size = 10
                tar.addfile(t1, io.BytesIO(b"1234567890"))
                t2 = tarfile.TarInfo("root/file2")
                t2.type = tarfile.REGTYPE
                t2.size = 10
                tar.addfile(t2, io.BytesIO(b"1234567890"))
            with patch("app.services.repository_snapshot.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.headers = {"Content-Length": "10"}
                mock_resp.iter_content.return_value = [archive_path.read_bytes()]
                mock_get.return_value.__enter__.return_value = mock_resp
                with pytest.raises(RepositoryArchiveTooLargeError, match="Extracted archive exceeds 15 bytes"):
                    snap._do_materialize(archive_path, Path(tmp_dir) / "ext5")

        # 6. Archive member count too large
        with patch.object(snap_module, "MAX_ARCHIVE_MEMBERS", 1):
            with tarfile.open(archive_path, "w:gz") as tar:
                t1 = tarfile.TarInfo("root/file1")
                t1.type = tarfile.REGTYPE
                t1.size = 1
                tar.addfile(t1, io.BytesIO(b"x"))
                t2 = tarfile.TarInfo("root/file2")
                t2.type = tarfile.REGTYPE
                t2.size = 1
                tar.addfile(t2, io.BytesIO(b"x"))
            with patch("app.services.repository_snapshot.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.headers = {"Content-Length": "10"}
                mock_resp.iter_content.return_value = [archive_path.read_bytes()]
                mock_get.return_value.__enter__.return_value = mock_resp
                with pytest.raises(RepositoryArchiveTooLargeError, match="Archive has too many members"):
                    snap._do_materialize(archive_path, Path(tmp_dir) / "ext6")
