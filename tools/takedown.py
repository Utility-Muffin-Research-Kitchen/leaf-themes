#!/usr/bin/env python3
"""Withdraw a published theme: a leaf-docs PR setting `withdrawn: true`.

    python3 tools/takedown.py --theme-id <id> --reason <text>

Maintainer only. The record of why lives in the leaf-docs PR body and in its
squash commit message, which is where the change itself lands. Release files
stay: installed copies keep working, and the catalog validator still checks
every published artifact of a withdrawn theme.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import catalog as catalog_mod  # noqa: E402
import leafdocs  # noqa: E402
import policy  # noqa: E402
import report  # noqa: E402

REASON_MAX_CHARS = 1000
REASON_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def reason_ok(reason: str) -> bool:
    return isinstance(reason, str) and 0 < len(reason.strip()) and \
        len(reason) <= REASON_MAX_CHARS and not REASON_CONTROL_RE.search(reason)


def main(argv=None) -> int:
    import github
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--leaf-docs-dir", default=None)
    parser.add_argument("--timeout", type=float, default=45 * 60)
    args = parser.parse_args(argv)

    if not policy.ID_RE.fullmatch(args.theme_id):
        raise SystemExit("theme id is not a THEME-1 id")
    if not reason_ok(args.reason):
        raise SystemExit(f"reason must be 1 to {REASON_MAX_CHARS} characters of text")
    themes_gh = github.from_env()
    actor = os.environ.get("GITHUB_ACTOR", "")
    if not policy.is_maintainer_permission(themes_gh.permission(actor)):
        raise SystemExit("only a maintainer can withdraw a theme")
    token = os.environ.get("LEAF_DOCS_TOKEN")
    if not token:
        raise SystemExit("LEAF_DOCS_TOKEN is not set")
    docs_gh = github.GitHub(token, leafdocs.REPOSITORY, api=themes_gh.api)

    def edit(current):
        try:
            return catalog_mod.withdraw(current, args.theme_id)
        except catalog_mod.CatalogError as error:
            raise leafdocs.CatalogPRFailed(str(error)) from None

    subject = f"Withdraw theme {args.theme_id} from Pak Rat"
    reason_line = " ".join(args.reason.split())
    try:
        outcome = leafdocs.publish_change(
            docs_gh, branch=f"themes/{args.theme_id}-withdraw", edit=edit,
            commit_message=subject, pr_title=subject,
            pr_body=report.takedown_pr_body(args.theme_id, args.reason, actor),
            merge_title=subject,
            merge_message=f"Requested by {actor}. Reason: {reason_line}",
            leaf_docs_dir=args.leaf_docs_dir, timeout=args.timeout)
    except leafdocs.CatalogPRFailed as error:
        print(f"::error::{error}" + (f" ({error.pr_url})" if error.pr_url else ""))
        return 1
    print("already withdrawn" if outcome.already else f"withdrawn: {outcome.pr_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
