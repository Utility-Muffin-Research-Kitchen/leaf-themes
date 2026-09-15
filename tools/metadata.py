"""themes/<id>/<version>.json: the reviewed record of one theme version.

It holds everything needed to publish the version and build its catalog
entry later without looking at the submission again: the theme.json store
fields, the form's license and summary, the canonical zip and preview facts,
the owner, and where the submission came from.
"""
from __future__ import annotations

import json
import re

import policy

SCHEMA = 1
SHA_RE = re.compile(r"[0-9a-f]{64}")
PATH_RE = re.compile(r"themes/([a-z0-9][a-z0-9-]{1,39})/((?:0|[1-9][0-9]{0,3})\."
                     r"(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3}))\.json")
DEFAULT_REPOSITORY = "Utility-Muffin-Research-Kitchen/leaf-themes"


def release_tag(theme_id: str, version: str) -> str:
    return f"{theme_id}-v{version}"


def staging_tag(theme_id: str, version: str) -> str:
    return f"staging-{theme_id}-v{version}"


def review_branch(theme_id: str, version: str) -> str:
    return f"submission/{theme_id}-v{version}"


def zip_name(theme_id: str, version: str) -> str:
    return f"{theme_id}-{version}.zip"


def preview_name(theme_id: str, version: str) -> str:
    return f"{theme_id}-{version}.preview.png"


def metadata_path(theme_id: str, version: str) -> str:
    return f"themes/{theme_id}/{version}.json"


def download_url(repository: str, theme_id: str, version: str, name: str) -> str:
    return (f"https://github.com/{repository}/releases/download/"
            f"{release_tag(theme_id, version)}/{name}")


def build(*, manifest: dict, license_value: str, summary: str, zip_bytes_size: int,
          zip_sha256: str, installed_size: int, preview_size: int, preview_sha256: str,
          owner: dict, submitter: dict, issue: int, source_sha256: str, source_size: int,
          repository: str = DEFAULT_REPOSITORY) -> dict:
    theme_id, version = manifest["id"], manifest["version"]
    record = {
        "schema": SCHEMA,
        "id": theme_id,
        "name": manifest["name"],
        "author": manifest["author"],
        "version": version,
        "min_leaf_version": manifest["min_leaf_version"],
        "license": license_value,
        "summary": summary,
    }
    description = manifest.get("description")
    if isinstance(description, str) and description.strip():
        record["description"] = description
    record.update({
        "artifact": {
            "name": zip_name(theme_id, version),
            "url": download_url(repository, theme_id, version, zip_name(theme_id, version)),
            "size": zip_bytes_size,
            "installed_size": installed_size,
            "sha256": zip_sha256,
        },
        "preview": {
            "name": preview_name(theme_id, version),
            "url": download_url(repository, theme_id, version, preview_name(theme_id, version)),
            "size": preview_size,
            "sha256": preview_sha256,
        },
        "owner": {"github_id": owner["github_id"], "login": owner.get("login")},
        "submitted_by": {"github_id": submitter["github_id"], "login": submitter.get("login")},
        "submission": {"issue": issue, "source_sha256": source_sha256,
                       "source_size": source_size},
    })
    return record


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def problems(record, path: str | None = None) -> list[str]:
    """What is wrong with a metadata record (empty when it can be published)."""
    out = []
    if not isinstance(record, dict):
        return ["not a JSON object"]
    if record.get("schema") != SCHEMA:
        out.append("schema must be 1")
    theme_id, version = record.get("id"), record.get("version")
    if not isinstance(theme_id, str) or not policy.ID_RE.fullmatch(theme_id):
        out.append("id is invalid")
        return out
    if policy.parse_version(version) is None:
        out.append("version is invalid")
        return out
    if path is not None and path != metadata_path(theme_id, version):
        out.append(f"path must be {metadata_path(theme_id, version)}")
    for key in ("name", "author", "summary"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            out.append(f"{key} must be a non-empty string")
    if "description" in record and (not isinstance(record["description"], str)
                                    or not record["description"].strip()):
        out.append("description must be a non-empty string when present")
    minimum = policy.parse_version(record.get("min_leaf_version"))
    if minimum is None or minimum < (0, 12, 0):
        out.append("min_leaf_version must be at least 0.12.0")
    if record.get("license") not in ("CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0",
                                     "redistribution-permitted"):
        out.append("license is not on the list")
    for key, name in (("artifact", zip_name(theme_id, version)),
                      ("preview", preview_name(theme_id, version))):
        item = record.get(key)
        if not isinstance(item, dict):
            out.append(f"{key} must be an object")
            continue
        if item.get("name") != name:
            out.append(f"{key}.name must be {name}")
        if not isinstance(item.get("url"), str) or not item["url"].startswith(
                "https://github.com/") or not item["url"].endswith(
                f"/releases/download/{release_tag(theme_id, version)}/{name}"):
            out.append(f"{key}.url does not name the release asset")
        if not _positive_int(item.get("size")):
            out.append(f"{key}.size must be a positive integer")
        if not isinstance(item.get("sha256"), str) or not SHA_RE.fullmatch(item["sha256"]):
            out.append(f"{key}.sha256 must be a lowercase sha256")
    if isinstance(record.get("artifact"), dict) and \
            not _positive_int(record["artifact"].get("installed_size")):
        out.append("artifact.installed_size must be a positive integer")
    for key in ("owner", "submitted_by"):
        item = record.get(key)
        if not isinstance(item, dict) or not _positive_int(item.get("github_id")):
            out.append(f"{key}.github_id must be a positive integer")
    submission = record.get("submission")
    if not isinstance(submission, dict) or not _positive_int(submission.get("issue")):
        out.append("submission.issue must be a positive integer")
    return out


def owners_with(owners: dict, theme_id: str, owner: dict) -> dict:
    """owners.json content with `theme_id` recorded, keys sorted."""
    updated = dict(owners)
    updated.setdefault(theme_id, {"github_id": owner["github_id"], "login": owner.get("login")})
    return dict(sorted(updated.items()))
