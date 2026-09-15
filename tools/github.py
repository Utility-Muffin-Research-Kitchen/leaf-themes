"""A small GitHub REST client on the standard library.

Only what the workflows need. Every call names its path explicitly; nothing
from a submission is ever put into a URL except values that have already
passed a strict pattern (theme ids, versions, issue numbers).
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

import download

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
API_VERSION = "2022-11-28"


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, token: str | None, repository: str, api: str = API,
                 uploads: str = UPLOADS):
        self.token = token
        self.repository = repository
        self.api = api
        self.uploads = uploads

    # -- plumbing ----------------------------------------------------------

    def _headers(self, accept="application/vnd.github+json", content_type=None):
        headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION,
                   "User-Agent": download.USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def request(self, method: str, path: str, body=None, *, params=None, base=None,
                data: bytes | None = None, content_type=None, allow=(), retries=3):
        url = (base or self.api) + path
        if params:
            url += "?" + urlencode(params)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            content_type = "application/json"
        for attempt in range(retries):
            request = urllib.request.Request(url, data=data, method=method,
                                             headers=self._headers(content_type=content_type))
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    raw = response.read()
                    return response.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as error:
                raw = error.read()
                if error.code in allow:
                    try:
                        return error.code, json.loads(raw) if raw else None
                    except ValueError:
                        return error.code, None
                # Retry transient failures, but never a POST: it may have landed.
                if error.code in (502, 503, 504) and method != "POST" \
                        and attempt + 1 < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise GitHubError(error.code, raw[:500].decode("utf-8", "replace")) from None
            except (urllib.error.URLError, OSError) as error:
                if method != "POST" and attempt + 1 < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise GitHubError(0, str(error)) from None
        raise GitHubError(0, "unreachable")

    def repo(self, suffix: str = "") -> str:
        return f"/repos/{self.repository}{suffix}"

    def paginate(self, path: str, params=None, key=None):
        page = 1
        while True:
            query = dict(params or {}, per_page=100, page=page)
            _, payload = self.request("GET", path, params=query)
            items = payload.get(key, []) if key else payload
            yield from items
            if len(items) < 100:
                return
            page += 1

    # -- people ------------------------------------------------------------

    def permission(self, login: str) -> dict:
        status, payload = self.request(
            "GET", self.repo(f"/collaborators/{quote(login, safe='')}/permission"),
            allow=(404,))
        return payload if status == 200 and isinstance(payload, dict) else {}

    def user_id(self, login: str) -> int | None:
        status, payload = self.request("GET", f"/users/{quote(login, safe='')}", allow=(404,))
        return payload.get("id") if status == 200 and isinstance(payload, dict) else None

    # -- issues ------------------------------------------------------------

    def issue(self, number: int) -> dict:
        return self.request("GET", self.repo(f"/issues/{int(number)}"))[1]

    def comments(self, number: int):
        return list(self.paginate(self.repo(f"/issues/{int(number)}/comments")))

    def comment(self, number: int, body: str) -> dict:
        return self.request("POST", self.repo(f"/issues/{int(number)}/comments"),
                            {"body": body})[1]

    def add_labels(self, number: int, labels: list[str]) -> None:
        self.request("POST", self.repo(f"/issues/{int(number)}/labels"), {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        self.request("DELETE", self.repo(f"/issues/{int(number)}/labels/{quote(label)}"),
                     allow=(404,))

    def close_issue(self, number: int) -> None:
        self.request("PATCH", self.repo(f"/issues/{int(number)}"),
                     {"state": "closed", "state_reason": "completed"})

    # -- releases ----------------------------------------------------------

    def releases(self):
        # Draft releases are only listed here, never by /releases/tags/{tag}.
        return list(self.paginate(self.repo("/releases")))

    def release_by_tag(self, tag: str, draft: bool) -> dict | None:
        for release in self.releases():
            if release.get("tag_name") == tag and bool(release.get("draft")) == draft:
                return release
        return None

    def create_release(self, tag: str, name: str, body: str, target: str,
                       draft: bool) -> dict:
        return self.request("POST", self.repo("/releases"), {
            "tag_name": tag, "target_commitish": target, "name": name, "body": body,
            "draft": draft, "prerelease": False, "make_latest": "false"})[1]

    def update_release(self, release_id: int, fields: dict) -> dict:
        return self.request("PATCH", self.repo(f"/releases/{int(release_id)}"), fields)[1]

    def delete_release(self, release_id: int) -> None:
        self.request("DELETE", self.repo(f"/releases/{int(release_id)}"), allow=(404,))

    def release_assets(self, release_id: int) -> list[dict]:
        return list(self.paginate(self.repo(f"/releases/{int(release_id)}/assets")))

    def delete_asset(self, asset_id: int) -> None:
        self.request("DELETE", self.repo(f"/releases/assets/{int(asset_id)}"), allow=(404,))

    def upload_asset(self, release_id: int, name: str, data: bytes,
                     content_type: str) -> dict:
        return self.request("POST", self.repo(f"/releases/{int(release_id)}/assets"),
                            params={"name": name}, base=self.uploads, data=data,
                            content_type=content_type)[1]

    def download_asset(self, asset_id: int, destination: str, cap: int) -> tuple[int, str]:
        """Download a release asset (drafts too) -> (size, sha256)."""
        url = f"{self.api}{self.repo(f'/releases/assets/{int(asset_id)}')}"
        headers = self._headers(accept="application/octet-stream")
        host = url.split("/")[2]
        return download.download(url, destination, cap, first_hosts=(host,), headers=headers)

    # -- git data ----------------------------------------------------------

    def branch_sha(self, branch: str) -> str | None:
        status, payload = self.request("GET", self.repo(f"/git/ref/heads/{quote(branch)}"),
                                       allow=(404,))
        return payload["object"]["sha"] if status == 200 else None

    def file_at(self, path: str, ref: str) -> bytes | None:
        status, payload = self.request("GET", self.repo(f"/contents/{quote(path)}"),
                                       params={"ref": ref}, allow=(404,))
        if status != 200 or not isinstance(payload, dict):
            return None
        if payload.get("encoding") == "base64" and payload.get("content") is not None:
            return base64.b64decode(payload["content"])
        # Files over 1 MiB come back without content; use the blob API.
        _, blob = self.request("GET", self.repo(f"/git/blobs/{payload['sha']}"))
        return base64.b64decode(blob["content"])

    def commit_files(self, branch: str, base_sha: str | None, files: dict[str, bytes],
                     message: str, force: bool = True) -> str:
        """Commit `files` on top of base_sha (or as a root commit) to `branch`."""
        tree_items = []
        for path, content in sorted(files.items()):
            _, blob = self.request("POST", self.repo("/git/blobs"), {
                "content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
            tree_items.append({"path": path, "mode": "100644", "type": "blob",
                               "sha": blob["sha"]})
        tree_body = {"tree": tree_items}
        parents = []
        if base_sha:
            _, base_commit = self.request("GET", self.repo(f"/git/commits/{base_sha}"))
            tree_body["base_tree"] = base_commit["tree"]["sha"]
            parents = [base_sha]
        _, tree = self.request("POST", self.repo("/git/trees"), tree_body)
        _, commit = self.request("POST", self.repo("/git/commits"), {
            "message": message, "tree": tree["sha"], "parents": parents})
        if self.branch_sha(branch) is None:
            self.request("POST", self.repo("/git/refs"),
                         {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        else:
            self.request("PATCH", self.repo(f"/git/refs/heads/{quote(branch)}"),
                         {"sha": commit["sha"], "force": force})
        return commit["sha"]

    def delete_branch(self, branch: str) -> None:
        self.request("DELETE", self.repo(f"/git/refs/heads/{quote(branch)}"),
                     allow=(404, 422))

    def compare_files(self, base: str, head: str) -> list[dict]:
        _, payload = self.request("GET", self.repo(f"/compare/{base}...{head}"))
        return payload.get("files", [])

    def commit_files_changed(self, sha: str) -> list[dict]:
        _, payload = self.request("GET", self.repo(f"/commits/{sha}"))
        return payload.get("files", [])

    # -- pull requests -----------------------------------------------------

    def pulls(self, state: str = "open", head_branch: str | None = None):
        params = {"state": state}
        if head_branch:
            params["head"] = f"{self.repository.split('/')[0]}:{head_branch}"
        return list(self.paginate(self.repo("/pulls"), params=params))

    def create_pull(self, title: str, head: str, base: str, body: str) -> dict:
        return self.request("POST", self.repo("/pulls"), {
            "title": title, "head": head, "base": base, "body": body,
            "maintainer_can_modify": True})[1]

    def update_pull(self, number: int, fields: dict) -> dict:
        return self.request("PATCH", self.repo(f"/pulls/{int(number)}"), fields)[1]

    def pull(self, number: int) -> dict:
        return self.request("GET", self.repo(f"/pulls/{int(number)}"))[1]

    def check_runs(self, sha: str, name: str) -> list[dict]:
        _, payload = self.request("GET", self.repo(f"/commits/{sha}/check-runs"),
                                  params={"check_name": name, "filter": "latest"})
        return payload.get("check_runs", [])

    def merge_pull(self, number: int, sha: str, title: str, message: str):
        return self.request("PUT", self.repo(f"/pulls/{int(number)}/merge"), {
            "merge_method": "squash", "sha": sha, "commit_title": title,
            "commit_message": message}, allow=(405, 409, 422))


def from_env(repository: str | None = None, token_env: str = "GITHUB_TOKEN") -> GitHub:
    return GitHub(os.environ.get(token_env), repository or os.environ["GITHUB_REPOSITORY"],
                  api=os.environ.get("GITHUB_API_URL", API))
