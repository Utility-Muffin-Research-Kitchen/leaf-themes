#!/usr/bin/env python3
"""The submission pipeline, one subcommand per workflow step.

    gate      decide whether this event is a submission run (reads the event)
    fetch     issue body, attachment, live catalog, open reviews -> workdir
    evaluate  every check, offline, plus the canonical rebuild -> workdir
    report    post the failure comment and labels
    stage     draft release, preview, review branch and PR, success comment

`evaluate` touches no network, so it runs the same way locally:

    python3 tools/submission.py evaluate --workdir <dir>

with <dir> holding issue.json, fetch.json, catalog.json, open_reviews.json and
(when downloaded) source.zip. See tests/test_end_to_end.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import download  # noqa: E402
import form  # noqa: E402
import metadata  # noqa: E402
import package  # noqa: E402
import policy  # noqa: E402
import report  # noqa: E402
from contracts import REPO_ROOT, theme_model  # noqa: E402

LABEL_SUBMISSION = "theme-submission"
LABEL_READY = "ready-for-review"
LABEL_CHANGES = "needs-changes"
BOT_LOGIN = "github-actions[bot]"
PREVIEWS_BRANCH = "previews"
DEFAULT_BRANCH = "main"
CATALOG_URLS = (
    "https://leaf.game/pakrat/v1/storefront.json",
    "https://raw.githubusercontent.com/Utility-Muffin-Research-Kitchen/leaf-docs/main/"
    "public/pakrat/v1/storefront.json",
)
CATALOG_MAX_BYTES = 8 * 1024 * 1024
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REVIEW_BRANCH_RE = re.compile(r"submission/([a-z0-9][a-z0-9-]{1,39})-v([0-9.]+)")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(metadata.dumps(obj))


def _set_outputs(**values) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in values.items()]
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def _summary(text: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)


def _github():
    import github
    return github.from_env()


def _labels(issue: dict) -> set[str]:
    return {label.get("name") for label in issue.get("labels", []) if isinstance(label, dict)}


def _is_maintainer(gh, login) -> bool:
    if not isinstance(login, str) or not LOGIN_RE.fullmatch(login):
        return False
    return policy.is_maintainer_permission(gh.permission(login))


def _post_once(gh, number: int, body: str) -> None:
    """Post `body` unless the newest pipeline comment already says exactly that."""
    ours = [c for c in gh.comments(number)
            if (c.get("user") or {}).get("login") == BOT_LOGIN
            and report.COMMENT_MARKER in (c.get("body") or "")]
    if ours and ours[-1].get("body", "").strip() == body.strip():
        return
    gh.comment(number, body)


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------

def cmd_gate(args) -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event = _read_json(os.environ["GITHUB_EVENT_PATH"], {})
    submissions_open = os.environ.get("SUBMISSIONS_OPEN") == "true"
    gh = _github()
    outputs = {"proceed": "false", "issue": "", "dry_run": "false",
               "as_github_id": "", "as_login": ""}

    def stop(message: str) -> int:
        _summary(f"Not a submission run: {message}")
        _set_outputs(**outputs)
        return 0

    if event_name == "workflow_dispatch":
        actor = os.environ.get("GITHUB_ACTOR", "")
        if not _is_maintainer(gh, actor):
            return stop("only a maintainer can run a dry run")
        inputs = event.get("inputs") or {}
        issue_input = str(inputs.get("issue", "")).strip()
        if not issue_input.isdigit() or int(issue_input) <= 0:
            return stop("issue must be a positive number")
        as_id = str(inputs.get("as_github_id") or "").strip()
        as_login = str(inputs.get("as_login") or "").strip()
        if as_id and (not as_id.isdigit() or int(as_id) <= 0):
            return stop("as_github_id must be a positive number")
        if as_login and not LOGIN_RE.fullmatch(as_login):
            return stop("as_login is not a GitHub login")
        issue = gh.issue(int(issue_input))
        if issue.get("pull_request") or LABEL_SUBMISSION not in _labels(issue):
            return stop(f"issue {issue_input} is not a theme submission")
        outputs.update(proceed="true", issue=str(int(issue_input)), dry_run="true",
                       as_github_id=as_id, as_login=as_login)
        _set_outputs(**outputs)
        return 0

    issue = event.get("issue") or {}
    number = issue.get("number")
    if not isinstance(number, int) or issue.get("pull_request"):
        return stop("no issue")
    if LABEL_SUBMISSION not in _labels(issue):
        return stop("the issue is not labeled theme-submission")
    if issue.get("state") != "open":
        return stop("the issue is closed")

    if event_name == "issue_comment":
        comment = event.get("comment") or {}
        if (comment.get("body") or "").strip() != "/recheck":
            return stop("the comment is not /recheck")
        if not _is_maintainer(gh, (comment.get("user") or {}).get("login")):
            return stop("only a maintainer can ask for /recheck")
    elif event_name != "issues":
        return stop(f"unexpected event {event_name}")

    author = (issue.get("user") or {}).get("login")
    if not submissions_open and not _is_maintainer(gh, author):
        # Submissions are not open yet: stay silent on outside issues.
        return stop("submissions are not open (SUBMISSIONS_OPEN is not true)")
    outputs.update(proceed="true", issue=str(number))
    _set_outputs(**outputs)
    return 0


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def fetch_catalog(destination: str, urls=CATALOG_URLS) -> str:
    last_error = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": download.USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(CATALOG_MAX_BYTES + 1)
            if len(raw) > CATALOG_MAX_BYTES:
                raise ValueError("catalog too large")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("apps"), list):
                raise ValueError("not a Pak Rat catalog")
            with open(destination, "wb") as handle:
                handle.write(raw)
            return url
        except (OSError, ValueError) as error:
            last_error = error
            print(f"catalog fetch from {url} failed: {error}", file=sys.stderr)
    raise RuntimeError(f"could not fetch the Pak Rat catalog: {last_error}")


def cmd_fetch(args) -> int:
    gh = _github()
    os.makedirs(args.workdir, exist_ok=True)
    issue = gh.issue(args.issue)
    user = issue.get("user") or {}
    submitter = {"github_id": user.get("id"), "login": user.get("login")}
    overridden = bool(args.as_github_id or args.as_login)
    if args.as_github_id:
        submitter["github_id"] = int(args.as_github_id)
    if args.as_login:
        submitter["login"] = args.as_login
        if not args.as_github_id:
            submitter["github_id"] = gh.user_id(args.as_login)
    if overridden:
        # A stand-in identity is a maintainer only if its login really is one.
        maintainer = bool(args.as_login) and _is_maintainer(gh, args.as_login)
    else:
        maintainer = _is_maintainer(gh, submitter["login"])
    if not isinstance(submitter["github_id"], int):
        raise SystemExit("no GitHub id for that login; pass as_github_id")
    _write_json(os.path.join(args.workdir, "issue.json"), {
        "number": args.issue, "html_url": issue.get("html_url"),
        "body": issue.get("body") or "", "submitter": submitter,
        "submitter_is_maintainer": maintainer, "dry_run": overridden or args.dry_run})

    parsed = form.parse(issue.get("body") or "")
    fetched = {"download": "skipped"}
    if parsed.attachment_url:
        destination = os.path.join(args.workdir, "source.zip")
        try:
            size, digest = download.download(parsed.attachment_url, destination,
                                             theme_model().MAX_ARCHIVE_BYTES)
            fetched = {"download": "ok", "size": size, "sha256": digest}
        except download.DownloadError as error:
            if os.path.exists(destination):
                os.remove(destination)
            fetched = {"download": "too_large" if error.too_large else "failed",
                       "detail": str(error)}
    _write_json(os.path.join(args.workdir, "fetch.json"), fetched)

    source = fetch_catalog(os.path.join(args.workdir, "catalog.json"))
    print(f"catalog from {source}", file=sys.stderr)

    reviews = []
    for pr in gh.pulls("open"):
        ref = (pr.get("head") or {}).get("ref") or ""
        issue_number = report.marker_issue(pr.get("body"))
        if REVIEW_BRANCH_RE.fullmatch(ref) and issue_number:
            reviews.append({"number": pr["number"], "branch": ref, "issue": issue_number})
    _write_json(os.path.join(args.workdir, "open_reviews.json"), reviews)
    return 0


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------

def evaluate(workdir: str, repo_root: str = REPO_ROOT,
             repository: str = metadata.DEFAULT_REPOSITORY) -> dict:
    issue = _read_json(os.path.join(workdir, "issue.json"))
    fetched = _read_json(os.path.join(workdir, "fetch.json"), {"download": "skipped"})
    live_catalog = _read_json(os.path.join(workdir, "catalog.json"))
    reviews = _read_json(os.path.join(workdir, "open_reviews.json"), [])
    owners = policy.load_owners(os.path.join(repo_root, "owners.json"))
    themes_dir = os.path.join(repo_root, "themes")
    tm = theme_model()

    reasons: list[str] = []
    warnings: list[str] = []
    result = {"status": "fail", "reasons": reasons, "warnings": warnings, "theme": None,
              "new_id": False, "maintainer_override": False, "previous_version": None,
              "withdrawn": False, "issue": issue["number"]}

    parsed = form.parse(issue.get("body"))
    reasons.extend(parsed.reasons)
    if fetched.get("download") == "too_large":
        reasons.append("theme-archive-too-large")
    elif fetched.get("download") == "failed":
        reasons.append("submission-download-failed")

    source = os.path.join(workdir, "source.zip")
    manifest = None
    if fetched.get("download") == "ok" and os.path.exists(source):
        inspection = package.inspect(source)
        reasons.extend(inspection.reasons)
        warnings.extend(inspection.warnings)
        manifest = inspection.manifest

    if isinstance(manifest, dict) and isinstance(manifest.get("id"), str) \
            and policy.ID_RE.fullmatch(manifest["id"]):
        theme_id, version = manifest["id"], manifest.get("version")
        result["theme"] = {"id": theme_id, "version": version, "name": manifest.get("name")}
        if policy.id_collision(live_catalog, theme_id):
            reasons.append("submission-id-taken")
        ownership = policy.decide_ownership(theme_id, issue["submitter"],
                                            issue["submitter_is_maintainer"], owners,
                                            live_catalog)
        result["new_id"] = ownership.new_id
        result["maintainer_override"] = ownership.maintainer_override
        result["owner"] = ownership.owner
        if not ownership.ok:
            reasons.append("submission-not-owner")
        published = policy.published_versions(themes_dir, theme_id, live_catalog)
        result["previous_version"] = published[-1] if published else None
        if policy.parse_version(version) and not policy.is_newer(version, published):
            reasons.append("submission-version-not-newer")
        if policy.at_version_limit(published):
            reasons.append("submission-version-limit")
        entry = policy.catalog_theme(live_catalog, theme_id)
        result["withdrawn"] = bool(entry and entry.get("withdrawn") is True)
        for review in reviews:
            match = REVIEW_BRANCH_RE.fullmatch(review.get("branch", ""))
            if match and match.group(1) == theme_id and review.get("issue") != issue["number"]:
                reasons.append("submission-in-review")
                break
        if parsed.license and manifest.get("license") in tm.LICENSES \
                and manifest["license"] != parsed.license:
            reasons.append("submission-license-mismatch")

    reasons[:] = report.order_reasons(reasons, tm.REASONS)
    warnings[:] = sorted(set(warnings))
    if reasons:
        return result

    # Everything passed: build what gets staged, then check it again.
    rebuilt = package.rebuild(source)
    theme_id, version = manifest["id"], manifest["version"]
    zip_path = os.path.join(workdir, metadata.zip_name(theme_id, version))
    with open(zip_path, "wb") as handle:
        handle.write(rebuilt)
    again = package.inspect(zip_path)
    if again.reasons or again.manifest != manifest:
        raise RuntimeError(f"the rebuilt zip did not pass THEME-1: {again.reasons}")
    preview = package.read_member(rebuilt, f"{theme_id}/preview.png")
    with open(os.path.join(workdir, metadata.preview_name(theme_id, version)), "wb") as handle:
        handle.write(preview)

    record = metadata.build(
        manifest=manifest, license_value=parsed.license, summary=parsed.summary,
        zip_bytes_size=len(rebuilt), zip_sha256=package.sha256(rebuilt),
        installed_size=package.installed_size(rebuilt), preview_size=len(preview),
        preview_sha256=package.sha256(preview), owner=result["owner"],
        submitter=issue["submitter"], issue=issue["number"],
        source_sha256=fetched["sha256"], source_size=fetched["size"], repository=repository)
    problems = metadata.problems(record)
    if problems:
        raise RuntimeError(f"metadata problems: {problems}")
    _write_json(os.path.join(workdir, "metadata.json"), record)
    result["status"] = "pass"
    return result


def cmd_evaluate(args) -> int:
    issue = _read_json(os.path.join(args.workdir, "issue.json"))
    result = evaluate(args.workdir, args.repo_root, args.repository)
    _write_json(os.path.join(args.workdir, "result.json"), result)
    if issue.get("dry_run"):
        _summary(report.dry_run_summary(result, issue["submitter"],
                                        issue["submitter_is_maintainer"]))
    elif result["status"] == "fail":
        _summary(report.failure_comment(result))
    else:
        theme = result["theme"]
        _summary(f"Passed: {report.code(theme['id'])} {report.code(theme['version'])}")
    _set_outputs(status=result["status"])
    return 0


# --------------------------------------------------------------------------
# report (failure)
# --------------------------------------------------------------------------

def _review_prs_for_issue(gh, number: int, state: str):
    for pr in gh.pulls(state):
        ref = (pr.get("head") or {}).get("ref") or ""
        if REVIEW_BRANCH_RE.fullmatch(ref) and report.marker_issue(pr.get("body")) == number:
            yield pr


def cmd_report(args) -> int:
    gh = _github()
    result = _read_json(os.path.join(args.workdir, "result.json"))
    number = int(result["issue"])
    if result["status"] != "fail":
        raise SystemExit("report runs only for a failed check")
    _post_once(gh, number, report.failure_comment(result))
    gh.add_labels(number, [LABEL_CHANGES])
    gh.remove_label(number, LABEL_READY)
    # A review PR staged from an earlier version of this issue no longer
    # matches what the issue holds, so it must not be merged by mistake.
    for pr in _review_prs_for_issue(gh, number, "open"):
        gh.comment(pr["number"], f"Closing: issue #{number} changed and no longer passes "
                                 "the checks.")
        gh.update_pull(pr["number"], {"state": "closed"})
    return 0


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def _stage_release(gh, record: dict, zip_bytes: bytes, preview_bytes: bytes) -> dict:
    theme_id, version = record["id"], record["version"]
    tag = metadata.staging_tag(theme_id, version)
    name = f"Staging: {theme_id} {version}"
    release = gh.release_by_tag(tag, draft=True)
    if release is None:
        release = gh.create_release(tag, name, report.staging_body(record), DEFAULT_BRANCH,
                                    draft=True)
    else:
        gh.update_release(release["id"], {"name": name, "body": report.staging_body(record)})
    wanted = {metadata.zip_name(theme_id, version): (zip_bytes, "application/zip"),
              metadata.preview_name(theme_id, version): (preview_bytes, "image/png")}
    for asset in gh.release_assets(release["id"]):
        gh.delete_asset(asset["id"])   # resubmission: replace everything
    for asset_name, (data, content_type) in wanted.items():
        uploaded = gh.upload_asset(release["id"], asset_name, data, content_type)
        if uploaded.get("size") != len(data) or uploaded.get("name") != asset_name:
            raise RuntimeError(f"upload of {asset_name} did not match: {uploaded}")
        digest = uploaded.get("digest")
        if digest and digest != f"sha256:{package.sha256(data)}":
            raise RuntimeError(f"upload of {asset_name} has digest {digest}")
    return release


def _push_preview(gh, record: dict, preview_bytes: bytes) -> str:
    """Put the preview on the orphan `previews` branch; return its raw URL.

    Why a branch: draft release assets are visible to maintainers only and
    cannot be embedded in a PR body, a PR cannot carry an image attachment
    through the API, and a public pre-release would publish the zip before
    review. The preview is not installable and the submitter's zip (which
    contains it) is already public in the issue, so a content-addressed PNG
    on a branch no workflow or device reads is the least exposure that still
    shows reviewers the image. Paths never repeat, so old PR bodies keep
    working; the branch can be squashed away at any time.
    """
    path = f"{record['id']}/{record['version']}/{record['preview']['sha256']}.png"
    url = (f"https://raw.githubusercontent.com/{gh.repository}/{PREVIEWS_BRANCH}/{path}")
    for attempt in range(3):
        head = gh.branch_sha(PREVIEWS_BRANCH)
        if head and gh.file_at(path, head) is not None:
            return url
        files = {path: preview_bytes}
        if head is None:
            files["README.md"] = (b"Review previews for theme submissions. Not published, "
                                  b"not read by devices. Safe to delete.\n")
        try:
            gh.commit_files(PREVIEWS_BRANCH, head, files,
                            f"Preview for {record['id']} {record['version']}", force=False)
            return url
        except Exception as error:  # a concurrent push moved the branch
            if attempt == 2:
                raise
            print(f"previews push retry: {error}", file=sys.stderr)
    return url


def _review_branch(gh, record: dict, new_id: bool) -> str:
    theme_id, version = record["id"], record["version"]
    branch = metadata.review_branch(theme_id, version)
    main_sha = gh.branch_sha(DEFAULT_BRANCH)
    owners = json.loads(gh.file_at("owners.json", main_sha) or b"{}")
    files = {metadata.metadata_path(theme_id, version): metadata.dumps(record).encode("utf-8")}
    recorded = owners.get(theme_id)
    if new_id:
        if recorded and recorded.get("github_id") != record["owner"]["github_id"]:
            raise RuntimeError(f"{theme_id} was claimed by another account while staging")
        files["owners.json"] = metadata.dumps(
            metadata.owners_with(owners, theme_id, record["owner"])).encode("utf-8")

    head = gh.branch_sha(branch)
    if head:
        _, commit = gh.request("GET", gh.repo(f"/git/commits/{head}"))
        parents = [p["sha"] for p in commit.get("parents", [])]
        if parents == [main_sha] and all(gh.file_at(p, head) == c for p, c in files.items()):
            return branch   # already staged from this exact state
    message = (f"Stage theme {theme_id} {version} for review\n\n"
               f"Submission issue #{record['submission']['issue']}.")
    gh.commit_files(branch, main_sha, files, message, force=True)
    return branch


def _cleanup_superseded(gh, number: int, keep_branch: str) -> None:
    for pr in _review_prs_for_issue(gh, number, "all"):
        ref = pr["head"]["ref"]
        if ref == keep_branch:
            continue
        if pr.get("state") == "open":
            gh.comment(pr["number"], f"Superseded by a newer staging of issue #{number}.")
            gh.update_pull(pr["number"], {"state": "closed"})
        if pr.get("merged_at"):
            continue
        if gh.pulls("open", head_branch=ref):
            continue   # the branch is in use by another open review
        match = REVIEW_BRANCH_RE.fullmatch(ref)
        gh.delete_branch(ref)
        draft = gh.release_by_tag(f"staging-{match.group(1)}-v{match.group(2)}", draft=True)
        if draft:
            gh.delete_release(draft["id"])


def cmd_stage(args) -> int:
    gh = _github()
    result = _read_json(os.path.join(args.workdir, "result.json"))
    record = _read_json(os.path.join(args.workdir, "metadata.json"))
    if result["status"] != "pass" or metadata.problems(record):
        raise SystemExit("stage runs only for a passed check with valid metadata")
    theme_id, version, number = record["id"], record["version"], int(record["submission"]["issue"])
    with open(os.path.join(args.workdir, metadata.zip_name(theme_id, version)), "rb") as handle:
        zip_bytes = handle.read()
    with open(os.path.join(args.workdir, metadata.preview_name(theme_id, version)), "rb") as handle:
        preview_bytes = handle.read()
    # The files crossed a job boundary: check them against the record again.
    if (len(zip_bytes), package.sha256(zip_bytes)) != (record["artifact"]["size"],
                                                       record["artifact"]["sha256"]) or \
            (len(preview_bytes), package.sha256(preview_bytes)) != (
                record["preview"]["size"], record["preview"]["sha256"]):
        raise SystemExit("staged files do not match metadata.json")
    zip_path = os.path.join(args.workdir, metadata.zip_name(theme_id, version))
    if package.inspect(zip_path).reasons:
        raise SystemExit("the canonical zip no longer passes THEME-1")

    release = _stage_release(gh, record, zip_bytes, preview_bytes)
    preview_url = _push_preview(gh, record, preview_bytes)
    branch = _review_branch(gh, record, bool(result.get("new_id")))

    issue_url = f"https://github.com/{gh.repository}/issues/{number}"
    body = report.review_pr_body(result, record, issue_url, preview_url, release["tag_name"])
    title = f"Theme: {record['id']} {record['version']}"
    existing = gh.pulls("open", head_branch=branch)
    if existing:
        pr = gh.update_pull(existing[0]["number"], {"title": title, "body": body})
    else:
        pr = gh.create_pull(title, branch, DEFAULT_BRANCH, body)
    _cleanup_superseded(gh, number, branch)

    _post_once(gh, number, report.success_comment(result, pr["html_url"], pr["number"],
                                                  gh.repository))
    gh.add_labels(number, [LABEL_READY])
    gh.remove_label(number, LABEL_CHANGES)
    _summary(f"Staged {theme_id} {version}: {pr['html_url']}")
    return 0


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gate")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--issue", type=int, required=True)
    fetch.add_argument("--workdir", required=True)
    fetch.add_argument("--as-github-id", default="")
    fetch.add_argument("--as-login", default="")
    fetch.add_argument("--dry-run", action="store_true")
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--workdir", required=True)
    evaluate_parser.add_argument("--repo-root", default=REPO_ROOT)
    evaluate_parser.add_argument("--repository",
                                 default=os.environ.get("GITHUB_REPOSITORY",
                                                        metadata.DEFAULT_REPOSITORY))
    for name in ("report", "stage"):
        sub.add_parser(name).add_argument("--workdir", required=True)
    args = parser.parse_args(argv)
    return {"gate": cmd_gate, "fetch": cmd_fetch, "evaluate": cmd_evaluate,
            "report": cmd_report, "stage": cmd_stage}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
