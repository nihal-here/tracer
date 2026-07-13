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

        # 4. Verify Read Files uses pinned SHA
        contents = repo.read_files(["file1.py"])
        self.assertEqual(contents["file1.py"], "print('hello')")

        urls_called = [call[0][0] for call in mock_get.call_args_list]
        self.assertEqual(urls_called.count("https://api.github.com/repos/testowner/testrepo"), 1)
        self.assertEqual(urls_called.count("https://api.github.com/repos/testowner/testrepo/branches/main"), 1)
        self.assertIn("https://api.github.com/repos/testowner/testrepo/git/trees/abc123pinnedsha?recursive=1", urls_called)
        self.assertIn("https://api.github.com/repos/testowner/testrepo/readme?ref=abc123pinnedsha", urls_called)
        self.assertIn("https://api.github.com/repos/testowner/testrepo/contents/file1.py?ref=abc123pinnedsha", urls_called)

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
