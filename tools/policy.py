"""Store rules a package validator cannot judge: ids, ownership, versions.

Ownership of a theme id is the numeric GitHub user id of the account that
first published it, recorded in owners.json. Logins can be renamed and are
kept only for display.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
MAX_VERSIONS = 16          # leaf-docs validate-pakrat-catalog.mjs MAX_PACKAGE_VERSIONS
MAINTAINER_PERMISSIONS = ("admin", "maintain", "write")


def parse_version(value) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = VERSION_RE.fullmatch(value)
    return tuple(int(part) for part in match.groups()) if match else None


def is_maintainer_permission(payload) -> bool:
    """True for a collaborator permission API response with write access or more.

    `role_name` distinguishes maintain from write; `permission` folds
    maintain into write and triage into read.
    """
    if not isinstance(payload, dict):
        return False
    return payload.get("role_name") in MAINTAINER_PERMISSIONS or \
        payload.get("permission") in ("admin", "write")


# --------------------------------------------------------------------------
# The live catalog
# --------------------------------------------------------------------------

def catalog_lane(catalog, lane: str) -> list[dict]:
    entries = catalog.get(lane) if isinstance(catalog, dict) else None
    return [entry for entry in entries if isinstance(entry, dict)] \
        if isinstance(entries, list) else []


def id_collision(catalog, theme_id: str) -> str | None:
    """The non-theme lane that already uses `theme_id`, if any."""
    folded = theme_id.casefold()
    for lane in ("apps", "content"):
        for entry in catalog_lane(catalog, lane):
            if isinstance(entry.get("id"), str) and entry["id"].casefold() == folded:
                return lane
    return None


def catalog_theme(catalog, theme_id: str) -> dict | None:
    for entry in catalog_lane(catalog, "themes"):
        if entry.get("id") == theme_id:
            return entry
    return None


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------

@dataclass
class Ownership:
    ok: bool
    owner: dict | None          # {"github_id", "login"} recorded or to record
    new_id: bool = False        # no one owns the id yet
    maintainer_override: bool = False


def load_owners(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        owners = json.load(handle)
    if not isinstance(owners, dict):
        raise ValueError("owners.json must be an object")
    return owners


def recorded_owner(owners: dict, catalog, theme_id: str) -> dict | None:
    record = owners.get(theme_id)
    if isinstance(record, dict) and isinstance(record.get("github_id"), int):
        return {"github_id": record["github_id"], "login": record.get("login")}
    # A theme already in the catalog but missing from owners.json (added by
    # hand) is still owned by the account the catalog names.
    entry = catalog_theme(catalog, theme_id)
    if entry and isinstance(entry.get("owner_github_id"), int):
        return {"github_id": entry["owner_github_id"], "login": None}
    return None


def decide_ownership(theme_id: str, submitter: dict, submitter_is_maintainer: bool,
                     owners: dict, catalog) -> Ownership:
    """May `submitter` ({"github_id", "login"}) submit a version of `theme_id`?"""
    owner = recorded_owner(owners, catalog, theme_id)
    if owner is None:
        return Ownership(True, {"github_id": submitter["github_id"],
                                "login": submitter["login"]}, new_id=True)
    if owner["github_id"] == submitter["github_id"]:
        return Ownership(True, owner)
    if submitter_is_maintainer:
        return Ownership(True, owner, maintainer_override=True)
    return Ownership(False, owner)


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

def reviewed_versions(themes_dir: str, theme_id: str) -> list[str]:
    """Versions with a merged themes/<id>/<version>.json."""
    folder = os.path.join(themes_dir, theme_id)
    if not os.path.isdir(folder):
        return []
    found = []
    for name in os.listdir(folder):
        if name.endswith(".json") and parse_version(name[:-len(".json")]):
            found.append(name[:-len(".json")])
    return found


def published_versions(themes_dir: str, theme_id: str, catalog) -> list[str]:
    versions = set(reviewed_versions(themes_dir, theme_id))
    entry = catalog_theme(catalog, theme_id)
    if entry and isinstance(entry.get("versions"), list):
        for item in entry["versions"]:
            if isinstance(item, dict) and parse_version(item.get("version")):
                versions.add(item["version"])
    return sorted(versions, key=parse_version)


def is_newer(version: str, published: list[str]) -> bool:
    parsed = parse_version(version)
    if parsed is None:
        return False
    return all(parsed > parse_version(other) for other in published)


def at_version_limit(published: list[str]) -> bool:
    # Published versions can never be dropped from the catalog (the
    # validator's immutability rule), so the 17th version cannot be added.
    return len(published) >= MAX_VERSIONS
