#!/usr/bin/env python3
"""Announce newly published themes in Discord through a channel webhook.

    DISCORD_WEBHOOK=<url> python3 tools/discord.py --announcements <file>

<file> is the JSON list `publish.py catalog` writes: one entry per theme
version whose leaf-docs catalog PR merged in this run. The payload is built
here with json.dumps from the reviewed metadata; name, author and summary
are submitted text, so they are escaped for Discord markdown and
`allowed_mentions` is empty, which keeps anything in them from pinging.

Never fails the publish: the theme is already live. A missing webhook is a
notice, a failed post is a warning.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import form  # noqa: E402
import metadata  # noqa: E402

COLOR = 5763719          # the Leaf release announcer's green
USER_AGENT = "DiscordBot (https://github.com/Utility-Muffin-Research-Kitchen/leaf-themes, 1)"
# Discord markdown, plus the characters that start mentions and links.
_ESCAPE_RE = re.compile(r"([\\*_~`|<>\[\]()@])")
_SPACE_RE = re.compile(r"\s+")


def flatten(text, limit: int) -> str:
    """One line, at most `limit` characters."""
    flat = _SPACE_RE.sub(" ", "" if text is None else str(text)).strip()
    if len(flat) > limit:
        flat = flat[:limit - 3].rstrip() + "..."
    return flat


def escape(text, limit: int) -> str:
    """Flattened text that Discord markdown shows literally."""
    escaped = _ESCAPE_RE.sub(r"\\\1", flatten(text, limit))
    # Headings, lists and quotes only start a line.
    return "\\" + escaped if escaped[:1] in ("#", "-", "+") else escaped


def release_url(record: dict, repository: str) -> str:
    return (f"https://github.com/{repository}/releases/tag/"
            f"{metadata.release_tag(record['id'], record['version'])}")


def payload(record: dict, repository: str, new_theme: bool) -> dict:
    heading = "New theme in Pak Rat" if new_theme else "Updated theme in Pak Rat"
    return {
        "content": f"{heading}: **{escape(record['name'], 60)}** {record['version']}",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            # Embed titles render no links or mentions; keep them unescaped.
            "title": flatten(f"{record['name']} {record['version']}", 200),
            "url": release_url(record, repository),
            "description": escape(record["summary"], 300),
            "color": COLOR,
            "fields": [
                {"name": "Author", "value": escape(record["author"], 100), "inline": True},
                {"name": "Version", "value": record["version"], "inline": True},
                {"name": "License", "value": form.LICENSE_LABELS[record["license"]],
                 "inline": True},
            ],
            "image": {"url": record["preview"]["url"]},
            "footer": {"text": "New theme" if new_theme else "Updated"},
        }],
    }


def post(webhook: str, body: dict, attempts: int = 3, sleep=time.sleep) -> tuple[bool, str]:
    data = json.dumps(body).encode("utf-8")
    detail = ""
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(webhook, data=data, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status in (200, 204):
                    return True, f"HTTP {response.status}"
                detail = f"HTTP {response.status}"
        except urllib.error.HTTPError as error:
            detail = f"HTTP {error.code}: {error.read(300).decode('utf-8', 'replace')}"
        except (urllib.error.URLError, OSError) as error:
            detail = str(error)
        if attempt < attempts:
            sleep(5)
    return False, detail


def announce(entries: list[dict], repository: str, webhook: str | None, repo_root: str,
             sender=post, log=print) -> int:
    """Post each entry; returns how many were posted. Never raises for Discord."""
    if not entries:
        log("No newly merged catalog changes to announce.")
        return 0
    if not webhook:
        log("::notice::DISCORD_THEMES_WEBHOOK is not set; skipping the Discord announcement.")
        return 0
    posted = 0
    for entry in entries:
        path = entry.get("path", "") if isinstance(entry, dict) else ""
        if not isinstance(path, str) or not metadata.PATH_RE.fullmatch(path):
            log(f"::warning::not announcing {path!r}: not a theme metadata path")
            continue
        try:
            with open(os.path.join(repo_root, path), encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError) as error:
            log(f"::warning::not announcing {path}: {error}")
            continue
        if metadata.problems(record, path):
            log(f"::warning::not announcing {path}: metadata problems")
            continue
        ok, detail = sender(webhook, payload(record, repository, bool(entry.get("new_theme"))))
        if ok:
            posted += 1
            log(f"Announced {record['id']} {record['version']} in Discord ({detail}).")
        else:
            # The webhook URL is a secret; never echo it.
            log(f"::warning::Discord announcement for {record['id']} {record['version']} "
                f"failed: {detail}")
    return posted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--announcements", required=True)
    parser.add_argument("--repo-root",
                        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = parser.parse_args(argv)
    try:
        with open(args.announcements, encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        entries = []
    if not isinstance(entries, list):
        entries = []
    announce(entries, os.environ.get("GITHUB_REPOSITORY", metadata.DEFAULT_REPOSITORY),
             os.environ.get("DISCORD_WEBHOOK"), args.repo_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
