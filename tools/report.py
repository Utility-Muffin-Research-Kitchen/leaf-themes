"""Text the pipeline posts: issue comments, PR and release bodies, commits.

Everything that came from a submission (names, ids, summaries, logins) goes
through `code()` or `pre()`, so it renders as literal text: no links, no
mentions, no markdown or HTML of its own.
"""
from __future__ import annotations

import html
import re

import form
import reasons as reason_text

COMMENT_MARKER = "<!-- leaf-themes:submission -->"
PR_MARKER_PREFIX = "<!-- leaf-themes:submission-issue="
PR_MARKER_RE = re.compile(r"<!-- leaf-themes:submission-issue=([0-9]{1,10}) -->")


def code(value, limit: int = 200) -> str:
    """Inline code for untrusted text. Safe inside a table cell."""
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).replace("`", "'")
    if len(text) > limit:
        text = text[:limit] + "..."
    text = text.replace("|", "\\|")
    return f"`{text.strip() or ' '}`"


def pre(value) -> str:
    return "<pre>" + html.escape("" if value is None else str(value)) + "</pre>"


def pr_marker(issue: int) -> str:
    return f"{PR_MARKER_PREFIX}{int(issue)} -->"


def marker_issue(body) -> int | None:
    match = PR_MARKER_RE.search(body or "")
    return int(match.group(1)) if match else None


def _warning_lines(warnings) -> list[str]:
    if not warnings:
        return []
    lines = ["", "### Warnings", "",
             "These do not block the submission, but you may want to fix them.", ""]
    lines += [f"- `{slug}`: {reason_text.explain(slug)}" for slug in warnings]
    return lines


def order_reasons(slugs, theme_reasons_order=()) -> list[str]:
    known = list(reason_text.SUBMISSION_REASONS) + list(theme_reasons_order) + \
        list(reason_text.THEME_REASONS)
    unique = list(dict.fromkeys(slugs))
    return sorted(unique, key=lambda slug: known.index(slug) if slug in known else len(known))


def failure_comment(result: dict) -> str:
    problems = result["reasons"]
    count = "1 problem" if len(problems) == 1 else f"{len(problems)} problems"
    lines = [COMMENT_MARKER, "## Your theme needs changes", "",
             f"The checks found {count} with this submission. Fix all of them, then edit "
             "this issue and attach the new zip in the **Theme zip** field. A maintainer "
             "will comment `/recheck` to run the checks again.", "",
             "| Check | What to fix |", "| --- | --- |"]
    lines += [f"| `{slug}` | {reason_text.explain(slug)} |" for slug in problems]
    lines += _warning_lines(result.get("warnings"))
    theme = result.get("theme")
    if theme:
        lines += ["", f"<sub>Checked {code(theme.get('id'))} version "
                      f"{code(theme.get('version'))}.</sub>"]
    return "\n".join(lines) + "\n"


def success_comment(result: dict, pr_url: str, pr_number: int, repository: str) -> str:
    theme = result["theme"]
    lines = [COMMENT_MARKER, "## Your theme passed the checks", "",
             f"{code(theme['name'])} ({code(theme['id'])}) version {code(theme['version'])} "
             f"is waiting for review in [{repository.split('/')[-1]}#{pr_number}]({pr_url}). "
             "When a maintainer merges it, the theme is published to Pak Rat and this issue "
             "closes.", "",
             "If you need to change something before then, edit this issue and attach the "
             "new zip. A maintainer will comment `/recheck`."]
    lines += _warning_lines(result.get("warnings"))
    return "\n".join(lines) + "\n"


def dry_run_summary(result: dict, submitter: dict, maintainer: bool) -> str:
    verdict = "PASS" if result["status"] == "pass" else "FAIL"
    lines = [f"## Dry run: {verdict}", "",
             f"Submitter: {code(submitter.get('login'))} (GitHub id "
             f"{code(submitter.get('github_id'))}), maintainer: {'yes' if maintainer else 'no'}.",
             "Nothing was staged, commented or labeled.", ""]
    theme = result.get("theme")
    if theme:
        lines.append(f"Theme {code(theme.get('id'))} version {code(theme.get('version'))}.")
        lines.append(f"New id: {result.get('new_id')}. Maintainer override: "
                     f"{result.get('maintainer_override')}.")
        lines.append("")
    if result["reasons"]:
        lines += ["| Check | Explanation |", "| --- | --- |"]
        lines += [f"| `{slug}` | {reason_text.explain(slug)} |" for slug in result["reasons"]]
    lines += _warning_lines(result.get("warnings"))
    return "\n".join(lines) + "\n"


def _size(value: int) -> str:
    return f"{value:,} bytes"


def review_pr_body(result: dict, record: dict, issue_url: str, preview_url: str,
                   staging: str) -> str:
    theme = result
    owner, submitter = record["owner"], record["submitted_by"]
    kind = "New theme" if theme.get("new_id") else \
        f"Update (newest published: {code(theme.get('previous_version'))})"
    ownership = f"{code(owner.get('login'))} (GitHub id {owner['github_id']})"
    if theme.get("new_id"):
        ownership += ", recorded in `owners.json` by this PR"
    if theme.get("maintainer_override"):
        ownership += f". Submitted by maintainer {code(submitter.get('login'))}, not the owner"
    lines = [
        pr_marker(record["submission"]["issue"]),
        f"Theme submission from [issue #{record['submission']['issue']}]({issue_url}) "
        f"by {code(submitter.get('login'))}.", "",
        "| | |", "| --- | --- |",
        f"| Kind | {kind} |",
        f"| Name | {code(record['name'])} |",
        f"| Id | {code(record['id'])} |",
        f"| Version | {code(record['version'])} |",
        f"| Author (theme.json) | {code(record['author'])} |",
        f"| Owner | {ownership} |",
        f"| License | {form.LICENSE_LABELS[record['license']]} (`{record['license']}`) |",
        f"| Minimum Leaf | {code(record['min_leaf_version'])} |",
        f"| Summary | {code(record['summary'])} |",
        f"| Zip | `{record['artifact']['name']}`, {_size(record['artifact']['size'])}, "
        f"{_size(record['artifact']['installed_size'])} extracted |",
        f"| Zip sha256 | `{record['artifact']['sha256']}` |",
        f"| Preview sha256 | `{record['preview']['sha256']}` |",
        f"| Submitted zip sha256 | `{record['submission']['source_sha256']}` |",
    ]
    if theme.get("withdrawn"):
        lines += ["", "**This theme is withdrawn in the catalog.** Publishing adds the "
                      "version but keeps it withdrawn."]
    if "description" in record:
        lines += ["", "Description:", "", pre(record["description"])]
    lines += ["", f"![Preview of {record['id']} {record['version']}]({preview_url})"]
    warnings = result.get("warnings") or []
    if warnings:
        lines += ["", "Warnings: " + ", ".join(f"`{slug}`" for slug in warnings)]
    lines += ["", "The canonical zip and preview are staged in the draft release "
                  f"`{staging}`. Merging this PR publishes release "
                  f"`{record['id']}-v{record['version']}` and adds it to the Pak Rat "
                  "catalog in leaf-docs."]
    return "\n".join(lines) + "\n"


def release_body(record: dict, repository: str) -> str:
    issue = record["submission"]["issue"]
    return "\n".join([
        f"{code(record['name'])} by {code(record['author'])}, version "
        f"{code(record['version'])}.", "",
        f"License: {form.LICENSE_LABELS[record['license']]}. Needs Leaf "
        f"{record['min_leaf_version']} or newer.", "",
        f"Submitted in [issue #{issue}](https://github.com/{repository}/issues/{issue}). "
        "Install it from Pak Rat on your device.",
    ]) + "\n"


def staging_body(record: dict) -> str:
    return (f"Staged for review: {code(record['id'])} {code(record['version'])} from issue "
            f"#{record['submission']['issue']}. Not published.\n")


def catalog_pr_body(record: dict, repository: str, metadata_url: str, new_theme: bool) -> str:
    issue = record["submission"]["issue"]
    release = f"https://github.com/{repository}/releases/tag/{record['id']}-v{record['version']}"
    kind = "a new theme" if new_theme else "a new version of an existing theme"
    return "\n".join([
        f"Adds {code(record['id'])} version {code(record['version'])} to the themes lane "
        f"of the Pak Rat catalog ({kind}).", "",
        f"- Release: {release}",
        f"- Reviewed metadata: {metadata_url}",
        f"- Submission: {repository}#{issue}", "",
        "Opened by the leaf-themes publish workflow. It merges this PR itself once the "
        "`validate` check passes.",
    ]) + "\n"


def takedown_pr_body(theme_id: str, reason: str, actor: str) -> str:
    return "\n".join([
        f"Withdraws theme {code(theme_id)} from the Pak Rat catalog (`withdrawn: true`).",
        "Installed copies keep working; the store stops offering it.", "",
        f"Requested by {code(actor)} through the leaf-themes takedown workflow. Reason:", "",
        pre(reason), "",
        "This PR merges itself once the `validate` check passes.",
    ]) + "\n"


def published_comment(record: dict, repository: str) -> str:
    release = f"https://github.com/{repository}/releases/tag/{record['id']}-v{record['version']}"
    return "\n".join([
        COMMENT_MARKER, "## Published", "",
        f"{code(record['name'])} version {code(record['version'])} is published. It shows "
        f"up in Pak Rat once leaf.game finishes deploying, usually within a few minutes.", "",
        f"Release: {release}", "",
        "To publish an update later, open a new submission with a higher version in "
        "theme.json.",
    ]) + "\n"


def catalog_stuck_comment(record: dict, pr_url: str, detail: str) -> str:
    return "\n".join([
        COMMENT_MARKER, "## Published, waiting on the catalog", "",
        f"The release for {code(record['id'])} version {code(record['version'])} is "
        f"published, but the catalog change could not merge ({detail}). A maintainer "
        f"needs to look at {pr_url}. You don't need to do anything.",
    ]) + "\n"
