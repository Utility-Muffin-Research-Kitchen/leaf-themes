"""Open a catalog PR in leaf-docs, wait for its `validate` check, merge it.

leaf-docs has no branch protection and auto-merge is off, so nothing merges
on its own: this module polls the check and squash-merges with an explicit
title and message (an explicit message keeps GitHub from appending
co-author lines). Each attempt rebuilds the change on the current main, so a
catalog edit that lands in between costs one more round instead of a
conflict.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

import catalog as catalog_mod

REPOSITORY = "Utility-Muffin-Research-Kitchen/leaf-docs"
CATALOG_PATH = "public/pakrat/v1/storefront.json"
CHECK_NAME = "validate"
BASE_BRANCH = "main"


class CatalogPRFailed(Exception):
    def __init__(self, message: str, pr_url: str | None = None):
        super().__init__(message)
        self.pr_url = pr_url


@dataclass
class Outcome:
    merged: bool
    already: bool = False
    pr_url: str | None = None


def run_validator(leaf_docs_dir: str, previous: dict, candidate: dict) -> None:
    """Run leaf-docs' own validator on the edit before any PR is opened."""
    script = os.path.join(leaf_docs_dir, "scripts", "validate-pakrat-catalog.mjs")
    with tempfile.TemporaryDirectory() as temp:
        previous_path = os.path.join(temp, "previous.json")
        candidate_path = os.path.join(temp, "candidate.json")
        with open(previous_path, "w", encoding="utf-8") as handle:
            handle.write(catalog_mod.dumps(previous))
        with open(candidate_path, "w", encoding="utf-8") as handle:
            handle.write(catalog_mod.dumps(candidate))
        completed = subprocess.run(
            ["node", script, "--catalog", candidate_path, "--previous-catalog", previous_path],
            cwd=leaf_docs_dir, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise CatalogPRFailed("leaf-docs validator rejected the edit:\n"
                              + completed.stdout + completed.stderr)


def wait_for_check(gh, sha: str, timeout: float, interval: float = 20,
                   sleep=None, clock=None) -> str:
    """'success', 'failure' (with any non-success conclusion) or 'timeout'."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + timeout
    while True:
        runs = gh.check_runs(sha, CHECK_NAME)
        if runs and all(run.get("status") == "completed" for run in runs):
            if all(run.get("conclusion") == "success" for run in runs):
                return "success"
            return "failure"
        if clock() >= deadline:
            return "timeout"
        sleep(interval)


def publish_change(gh, *, branch: str, edit, commit_message: str, pr_title: str,
                   pr_body: str, merge_title: str, merge_message: str,
                   leaf_docs_dir: str | None, timeout: float, attempts: int = 3,
                   log=print) -> Outcome:
    """Apply `edit(catalog) -> (catalog, changed)` to leaf-docs main through a PR.

    Safe to rerun: when the edit is already on main, nothing is opened.
    """
    pr = None
    for attempt in range(1, attempts + 1):
        base_sha = gh.branch_sha(BASE_BRANCH)
        raw = gh.file_at(CATALOG_PATH, base_sha)
        if raw is None:
            raise CatalogPRFailed(f"{CATALOG_PATH} not found on {BASE_BRANCH}")
        current = json.loads(raw)
        candidate, changed = edit(current)
        if not changed:
            log(f"leaf-docs {BASE_BRANCH} already has this change")
            existing = gh.pulls("open", head_branch=branch)
            for stale in existing:
                gh.update_pull(stale["number"], {"state": "closed"})
            gh.delete_branch(branch)
            return Outcome(merged=False, already=True)
        catalog_mod.stamp(candidate)
        if leaf_docs_dir:
            run_validator(leaf_docs_dir, current, candidate)

        head_sha = gh.commit_files(branch, base_sha,
                                   {CATALOG_PATH: catalog_mod.dumps(candidate).encode("utf-8")},
                                   commit_message)
        open_prs = gh.pulls("open", head_branch=branch)
        if open_prs:
            pr = gh.update_pull(open_prs[0]["number"], {"title": pr_title, "body": pr_body})
        else:
            pr = gh.create_pull(pr_title, branch, BASE_BRANCH, pr_body)
        log(f"attempt {attempt}: {pr['html_url']} at {head_sha}")

        verdict = wait_for_check(gh, head_sha, timeout)
        if verdict != "success":
            raise CatalogPRFailed(f"the {CHECK_NAME} check ended with {verdict}",
                                  pr["html_url"])
        status, payload = gh.merge_pull(pr["number"], head_sha,
                                        f"{merge_title} (#{pr['number']})", merge_message)
        if status == 200 and payload and payload.get("merged"):
            gh.delete_branch(branch)
            return Outcome(merged=True, pr_url=pr["html_url"])
        log(f"merge refused ({status}): {payload}; rebuilding on the current {BASE_BRANCH}")
    raise CatalogPRFailed(f"could not merge after {attempts} attempts",
                          pr["html_url"] if pr else None)
