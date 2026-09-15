"""Edit the themes lane of leaf-docs' storefront.json.

The rules mirror leaf-docs scripts/validate-pakrat-catalog.mjs: an allowlist
of theme keys, versions newest first, at most 16 of them, top-level fields
mirroring the newest version, ids unique across apps, content and themes,
and published versions immutable. The tests run that validator on what this
module writes.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import policy


class CatalogError(Exception):
    pass


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        catalog = json.load(handle)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("apps"), list):
        raise CatalogError("not a Pak Rat catalog")
    return catalog


def dumps(catalog: dict) -> str:
    # The format storefront.json is committed in.
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def stamp(catalog: dict, now: datetime | None = None) -> None:
    """Bump catalog_revision and generated_at, as prod-YYYYMMDDTHHMMSSZ."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    catalog["catalog_revision"] = "prod-" + now.strftime("%Y%m%dT%H%M%SZ")
    catalog["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")


def version_entry(record: dict) -> dict:
    artifact = record["artifact"]
    return {
        "version": record["version"],
        "min_leaf_version": record["min_leaf_version"],
        "artifact": {
            "url": artifact["url"],
            "name": artifact["name"],
            "archive": "zip",
            "size": artifact["size"],
            "installed_size": artifact["installed_size"],
            "sha256": artifact["sha256"],
        },
    }


def _theme_entry(record: dict, versions: list[dict], withdrawn: bool) -> dict:
    newest = versions[0]
    entry = {
        "id": record["id"],
        "name": record["name"],
        "author": record["author"],
        "owner_github_id": record["owner"]["github_id"],
        "summary": record["summary"],
    }
    if "description" in record:
        entry["description"] = record["description"]
    entry.update({
        "license": record["license"],
        "preview": {
            "url": record["preview"]["url"],
            "sha256": record["preview"]["sha256"],
            "size": record["preview"]["size"],
        },
        "version": newest["version"],
        "min_leaf_version": newest["min_leaf_version"],
        "install_name": record["id"],
        "artifact": copy.deepcopy(newest["artifact"]),
        "versions": versions,
        "withdrawn": withdrawn,
    })
    return entry


def add_version(catalog: dict, record: dict) -> tuple[dict, bool]:
    """(new catalog, changed). Unchanged when this exact version is already listed."""
    catalog = copy.deepcopy(catalog)
    theme_id, version = record["id"], record["version"]
    lane = policy.id_collision(catalog, theme_id)
    if lane:
        raise CatalogError(f"id {theme_id} is already used in {lane}")
    if "themes" in catalog and not isinstance(catalog["themes"], list):
        raise CatalogError("themes must be an array")
    themes = catalog.setdefault("themes", [])
    new_version = version_entry(record)

    index = next((i for i, entry in enumerate(themes)
                  if isinstance(entry, dict) and entry.get("id") == theme_id), None)
    if index is None:
        themes.append(_theme_entry(record, [new_version], False))
        return catalog, True

    existing = themes[index]
    versions = existing.get("versions") if isinstance(existing.get("versions"), list) else []
    for item in versions:
        if isinstance(item, dict) and item.get("version") == version:
            if item == new_version:
                return catalog, False
            raise CatalogError(f"{theme_id} {version} is already published with different facts")
    newest = max((policy.parse_version(item.get("version")) for item in versions
                  if isinstance(item, dict) and policy.parse_version(item.get("version"))),
                 default=None)
    if newest is not None and policy.parse_version(version) <= newest:
        raise CatalogError(f"{theme_id} {version} is not newer than the published versions")
    if len(versions) >= policy.MAX_VERSIONS:
        raise CatalogError(f"{theme_id} already has {policy.MAX_VERSIONS} versions")
    merged = [new_version] + copy.deepcopy(versions)
    withdrawn = existing.get("withdrawn") is True
    themes[index] = _theme_entry(record, merged, withdrawn)
    return catalog, True


def withdraw(catalog: dict, theme_id: str) -> tuple[dict, bool]:
    """(new catalog, changed) with `withdrawn: true` on the theme."""
    catalog = copy.deepcopy(catalog)
    entry = policy.catalog_theme(catalog, theme_id)
    if entry is None:
        raise CatalogError(f"no theme {theme_id} in the catalog")
    if entry.get("withdrawn") is True:
        return catalog, False
    entry["withdrawn"] = True
    return catalog, True


def has_version(catalog: dict, record: dict) -> bool:
    entry = policy.catalog_theme(catalog, record["id"])
    if not entry or not isinstance(entry.get("versions"), list):
        return False
    return version_entry(record) in entry["versions"]
