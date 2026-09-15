#!/usr/bin/env python3
"""Publish reviewed theme versions after a maintainer merges the review PR.

    added     list themes/<id>/<version>.json files added by a push
    release   verify the staged draft, create the immutable release, drop the draft
    catalog   add each version to leaf-docs through a PR the bot merges when
              `validate` passes, then close the submission issue

Every step checks what already exists first, so a rerun finishes the work
instead of repeating it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import catalog as catalog_mod  # noqa: E402
import download  # noqa: E402
import leafdocs  # noqa: E402
import metadata  # noqa: E402
import package  # noqa: E402
import policy  # noqa: E402
import report  # noqa: E402
from contracts import REPO_ROOT, theme_model  # noqa: E402

ZERO_SHA = "0" * 40
SHA_RE_TEXT = "0123456789abcdef"


class PublishError(Exception):
    pass


def _set_output(key: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")
    print(f"{key}={value}")


def _is_sha(value: str) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in SHA_RE_TEXT for c in value)


# --------------------------------------------------------------------------
# added
# --------------------------------------------------------------------------

def added_metadata(files: list[dict]) -> list[str]:
    """Metadata paths among a compare's files that were added."""
    paths = []
    for item in files:
        name = item.get("filename", "")
        if item.get("status") == "added" and metadata.PATH_RE.fullmatch(name):
            paths.append(name)
    return sorted(set(paths))


def cmd_added(args) -> int:
    import github
    if args.path:
        if not metadata.PATH_RE.fullmatch(args.path) or \
                not os.path.isfile(os.path.join(args.repo_root, args.path)):
            raise SystemExit(f"{args.path} is not a theme metadata file on this commit")
        paths = [args.path]
    else:
        gh = github.from_env()
        if not _is_sha(args.after):
            raise SystemExit("--after must be a commit sha")
        if _is_sha(args.before) and args.before != ZERO_SHA:
            files = gh.compare_files(args.before, args.after)
        else:
            files = gh.commit_files_changed(args.after)
        paths = added_metadata(files)
    print("metadata to publish:", paths)
    _set_output("paths", json.dumps(paths))
    return 0


def _paths(raw: str) -> list[str]:
    paths = json.loads(raw)
    if not isinstance(paths, list) or not all(
            isinstance(p, str) and metadata.PATH_RE.fullmatch(p) for p in paths):
        raise SystemExit("--paths must be a JSON list of themes/<id>/<version>.json")
    return paths


def load_record(repo_root: str, path: str) -> dict:
    with open(os.path.join(repo_root, path), encoding="utf-8") as handle:
        record = json.load(handle)
    problems = metadata.problems(record, path)
    if problems:
        raise PublishError(f"{path}: " + "; ".join(problems))
    owners = policy.load_owners(os.path.join(repo_root, "owners.json"))
    owner = owners.get(record["id"])
    if not isinstance(owner, dict) or owner.get("github_id") != record["owner"]["github_id"]:
        raise PublishError(f"{path}: owner does not match owners.json")
    return record


# --------------------------------------------------------------------------
# release
# --------------------------------------------------------------------------

def _asset_matches(gh, asset: dict, size: int, sha: str, workdir: str) -> bool:
    if asset.get("size") != size:
        return False
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest == f"sha256:{sha}"
    try:
        got = gh.download_asset(asset["id"], os.path.join(workdir, "check.bin"), size)
    except download.DownloadError:
        return False
    return got == (size, sha)


def _expected_assets(record: dict) -> dict:
    return {record["artifact"]["name"]: (record["artifact"]["size"], record["artifact"]["sha256"]),
            record["preview"]["name"]: (record["preview"]["size"], record["preview"]["sha256"])}


def release_matches(gh, release: dict, record: dict, workdir: str) -> bool:
    expected = _expected_assets(record)
    assets = gh.release_assets(release["id"])
    if sorted(a.get("name") for a in assets) != sorted(expected):
        return False
    return all(_asset_matches(gh, a, *expected[a["name"]], workdir) for a in assets)


def check_zip(path: str, record: dict) -> None:
    inspection = package.inspect(path)
    if inspection.reasons:
        raise PublishError(f"staged zip fails THEME-1: {inspection.reasons}")
    manifest = inspection.manifest or {}
    for key in ("id", "version", "min_leaf_version", "license"):
        if manifest.get(key) != record[key]:
            raise PublishError(f"staged zip theme.json {key} does not match the metadata")


def publish_release(gh, record: dict, target: str, workdir: str, log=print) -> str:
    theme_id, version = record["id"], record["version"]
    tag = metadata.release_tag(theme_id, version)
    staging = gh.release_by_tag(metadata.staging_tag(theme_id, version), draft=True)

    published = gh.release_by_tag(tag, draft=False)
    if published:
        if not release_matches(gh, published, record, workdir):
            raise PublishError(f"release {tag} already exists with different assets")
        log(f"release {tag} already published with the recorded assets")
        if staging:
            gh.delete_release(staging["id"])
        return published["html_url"]
    if staging is None:
        raise PublishError(f"no staged draft release for {theme_id} {version}")

    # Download the staged files and check every recorded fact again.
    files = {}
    by_name = {a["name"]: a for a in gh.release_assets(staging["id"])}
    for name, (size, sha) in _expected_assets(record).items():
        asset = by_name.get(name)
        if asset is None:
            raise PublishError(f"staged draft is missing {name}")
        path = os.path.join(workdir, name)
        try:
            got = gh.download_asset(asset["id"], path, size)
        except download.DownloadError as error:
            raise PublishError(f"staged {name} could not be downloaded: {error}") from None
        if got != (size, sha):
            raise PublishError(f"staged {name} does not match the metadata")
        with open(path, "rb") as handle:
            files[name] = handle.read()
    check_zip(os.path.join(workdir, record["artifact"]["name"]), record)

    # An interrupted earlier run can leave an unpublished draft under the final tag.
    leftover = gh.release_by_tag(tag, draft=True)
    if leftover:
        gh.delete_release(leftover["id"])
    release = gh.create_release(tag, f"{record['name']} {version}",
                                report.release_body(record, gh.repository), target, draft=True)
    types = {record["artifact"]["name"]: "application/zip",
             record["preview"]["name"]: "image/png"}
    for name, data in files.items():
        uploaded = gh.upload_asset(release["id"], name, data, types[name])
        if uploaded.get("size") != len(data):
            raise PublishError(f"upload of {name} did not match")
    if not release_matches(gh, release, record, workdir):
        raise PublishError(f"release {tag} assets did not upload intact")
    final = gh.update_release(release["id"], {"draft": False, "make_latest": "false"})
    if final.get("draft") or final.get("tag_name") != tag:
        raise PublishError(f"release {tag} did not publish")
    gh.delete_release(staging["id"])
    log(f"published {final['html_url']}")
    return final["html_url"]


def cmd_release(args) -> int:
    import github
    gh = github.from_env()
    if not _is_sha(args.target):
        raise SystemExit("--target must be a commit sha")
    theme_model()  # fail early when leaf-contracts is missing
    for path in _paths(args.paths):
        record = load_record(args.repo_root, path)
        with tempfile.TemporaryDirectory() as workdir:
            publish_release(gh, record, args.target, workdir)
    return 0


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------

def cmd_catalog(args) -> int:
    import github
    themes_gh = github.from_env()
    token = os.environ.get("LEAF_DOCS_TOKEN")
    if not token:
        raise SystemExit("LEAF_DOCS_TOKEN is not set")
    docs_gh = github.GitHub(token, leafdocs.REPOSITORY, api=themes_gh.api)
    failed = False
    announcements = []
    for path in _paths(args.paths):
        record = load_record(args.repo_root, path)
        issue = record["submission"]["issue"]
        metadata_url = f"https://github.com/{themes_gh.repository}/blob/{args.target}/{path}"

        def edit(current, record=record):
            try:
                return catalog_mod.add_version(current, record)
            except catalog_mod.CatalogError as error:
                raise leafdocs.CatalogPRFailed(str(error)) from None

        base_sha = docs_gh.branch_sha(leafdocs.BASE_BRANCH)
        before = json.loads(docs_gh.file_at(leafdocs.CATALOG_PATH, base_sha))
        new_theme = policy.catalog_theme(before, record["id"]) is None
        subject = f"Publish theme {record['id']} {record['version']} to Pak Rat"
        try:
            outcome = leafdocs.publish_change(
                docs_gh, branch=f"themes/{record['id']}-v{record['version']}", edit=edit,
                commit_message=subject,
                pr_title=subject,
                pr_body=report.catalog_pr_body(record, themes_gh.repository, metadata_url,
                                               new_theme),
                merge_title=subject,
                merge_message=(f"Release: https://github.com/{themes_gh.repository}/releases/"
                               f"tag/{record['id']}-v{record['version']}\n"
                               f"Submission: {themes_gh.repository}#{issue}"),
                leaf_docs_dir=args.leaf_docs_dir, timeout=args.timeout)
        except leafdocs.CatalogPRFailed as error:
            print(f"::error::{path}: {error}")
            if error.pr_url:
                themes_gh.comment(issue, report.catalog_stuck_comment(
                    record, error.pr_url, str(error).splitlines()[0]))
            failed = True
            continue
        print(f"{path}: {'already in the catalog' if outcome.already else outcome.pr_url}")
        if outcome.merged:
            # Announce only what this run put into the catalog, so a rerun
            # never posts the same theme twice.
            announcements.append({"path": path, "new_theme": new_theme})
            if args.announce_out:
                with open(args.announce_out, "w", encoding="utf-8") as handle:
                    json.dump(announcements, handle)
        issue_state = themes_gh.issue(issue)
        if issue_state.get("state") == "open":
            themes_gh.comment(issue, report.published_comment(record, themes_gh.repository))
            themes_gh.close_issue(issue)
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    added = sub.add_parser("added")
    added.add_argument("--before", default="")
    added.add_argument("--after", default="")
    added.add_argument("--path", default="")
    added.add_argument("--repo-root", default=REPO_ROOT)
    release = sub.add_parser("release")
    release.add_argument("--paths", required=True)
    release.add_argument("--target", required=True)
    release.add_argument("--repo-root", default=REPO_ROOT)
    catalog_parser = sub.add_parser("catalog")
    catalog_parser.add_argument("--paths", required=True)
    catalog_parser.add_argument("--target", required=True)
    catalog_parser.add_argument("--repo-root", default=REPO_ROOT)
    catalog_parser.add_argument("--leaf-docs-dir", default=None)
    catalog_parser.add_argument("--timeout", type=float, default=45 * 60)
    catalog_parser.add_argument("--announce-out", default=None,
                                help="write the versions merged in this run (for discord.py)")
    args = parser.parse_args(argv)
    try:
        return {"added": cmd_added, "release": cmd_release,
                "catalog": cmd_catalog}[args.command](args)
    except PublishError as error:
        print(f"::error::{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
