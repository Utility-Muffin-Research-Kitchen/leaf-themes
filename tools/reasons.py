"""Plain-English sentences for every reason and warning a submission can get.

THEME-1 slugs come from leaf-contracts (docs/themes.md). The `submission-`
slugs belong to this pipeline: they cover the form, the download, ownership
and versions, which a package validator cannot judge. The tests check that
every THEME-1 slug the reference validator knows has a sentence here.
"""
from __future__ import annotations

THEME_REASONS = {
    "theme-archive-too-large": "The zip is bigger than 10 MiB.",
    "theme-malformed-archive": "The zip is damaged or uses a zip feature Leaf does not read (ZIP64, split archives, or an entry that does not decompress cleanly). Create the zip again with your system's normal zip tool.",
    "theme-too-many-entries": "The zip has more than 512 files and folders.",
    "theme-unsupported-compression": "A file in the zip is encrypted or uses a compression method other than stored or deflate.",
    "theme-uncompressed-too-large": "The files in the zip add up to more than 25 MiB once extracted.",
    "theme-compression-ratio": "A file in the zip compresses more than 100 to 1, which Leaf refuses as a possible zip bomb.",
    "theme-entry-name-encoding": "A file name in the zip is empty, is not valid UTF-8, or contains control characters.",
    "theme-absolute-path": "A path in the zip starts with / or a drive letter.",
    "theme-backslash-path": "A path in the zip contains a backslash. Use forward slashes only.",
    "theme-path-traversal": "A path in the zip contains a . or .. folder.",
    "theme-hidden-file": "The zip contains a hidden file or folder, such as .DS_Store, ._ files or __MACOSX. On a Mac, zip the folder from the command line with zip -r -X your-theme.zip your-theme -x '*.DS_Store'.",
    "theme-symlink": "The zip contains a symbolic link.",
    "theme-special-file": "The zip contains something that is not a regular file or folder.",
    "theme-duplicate-entry": "Two entries in the zip have the same name when letter case is ignored. The SD card is FAT32, where they would be one file.",
    "theme-not-single-folder": "The zip must hold exactly one top-level folder, named after your theme id, with nothing beside it.",
    "theme-unknown-file": "The zip contains a file or folder that is not on the THEME-1 list. Remove anything the themes guide does not describe.",
    "theme-system-id-invalid": "An art file name is not a valid system code. Use uppercase letters, digits and underscores, 2 to 32 characters (plus _apps for the Apps icon).",
    "theme-reserved-system-id": "An art file is named _default, which can never be themed.",
    "theme-multiple-wallpapers": "A folder has more than one wallpaper. Keep one of wallpaper.png, wallpaper.jpg or wallpaper.jpeg per folder.",
    "theme-missing-manifest": "theme.json is missing from the theme folder.",
    "theme-manifest-too-large": "theme.json is larger than 64 KiB.",
    "theme-malformed-manifest": "theme.json is not a single valid JSON object. Check for a byte order mark, duplicate keys, or a syntax error.",
    "theme-missing-preview": "preview.png is missing from the theme folder.",
    "theme-id-mismatch": "The top-level folder name does not exactly match the id in theme.json.",
    "theme-reserved-name": "The theme id is the name of a theme that ships with Leaf. Pick a different id.",
    "theme-unsupported-image": "An image is not the format its file name says, or is a kind of PNG or JPEG Leaf cannot read.",
    "theme-image-dimensions": "An image is outside its size limits: preview.png must be exactly 960 x 720, wallpapers at most 2048 px per side, and icons, labels and wordmarks at most 1024 px per side.",
    "theme-unknown-schema": "theme.json must have \"schema\": 1.",
    "theme-unknown-field": "theme.json has a key that THEME-1 does not define.",
    "theme-id-invalid": "The id in theme.json must be 2 to 40 characters of lowercase letters, digits and hyphens, starting with a letter or digit.",
    "theme-name-invalid": "The name in theme.json must be 1 to 40 characters with no control characters.",
    "theme-author-invalid": "The author in theme.json must be 1 to 60 characters with no control characters.",
    "theme-version-invalid": "The version in theme.json must look like 1.0.0, with no leading zeros.",
    "theme-min-leaf-version": "min_leaf_version in theme.json must be a version like 0.12.0, and at least 0.12.0.",
    "theme-unknown-license": "The license in theme.json must be one of CC-BY-4.0, CC-BY-SA-4.0, CC0-1.0 or redistribution-permitted.",
    "theme-description-invalid": "The description in theme.json must be text of at most 300 characters.",
    "theme-grid-invalid": "grid in theme.json needs both cols (1 to 8) and rows (1 to 6) as whole numbers.",
    "theme-color-invalid": "A color in theme.json is not #RRGGBB or #RRGGBBAA.",
    "theme-color-level-invalid": "underlay_opacity and shadow in theme.json must be whole numbers from 0 to 255.",
    "theme-status-style-invalid": "status_style in theme.json must be auto, light or dark.",
}

THEME_WARNINGS = {
    "theme-icon-off-size": "An icon is not 512 x 512. Leaf still shows it, scaled to fit.",
    "theme-no-art": "The theme has no wallpaper, icons, labels or wordmarks, so it only changes colors.",
}

SUBMISSION_REASONS = {
    "submission-form-malformed": "The issue no longer matches the submission form (a section is missing or appears twice). Open a new submission with the form instead of rewriting the issue text.",
    "submission-missing-attachment": "No zip is attached. Attach your theme zip in the Theme zip field.",
    "submission-attachment-url": "The Theme zip field must hold a zip uploaded to GitHub (a https://github.com/user-attachments/files/ link). Links to other sites are not downloaded.",
    "submission-multiple-attachments": "The Theme zip field holds more than one zip. Attach exactly one.",
    "submission-download-failed": "The attached zip could not be downloaded. Try attaching it again.",
    "submission-license-missing": "Pick a license from the list in the License field.",
    "submission-license-mismatch": "The license you picked in the form does not match the license in theme.json. Make them the same.",
    "submission-summary-invalid": "The summary must be one line of 1 to 100 characters, with no control or text-direction characters.",
    "submission-confirmations": "All three confirmation boxes must be checked.",
    "submission-id-taken": "This theme id is already used by an app or content package in Pak Rat. Pick a different id.",
    "submission-not-owner": "This theme id belongs to another GitHub account. Only the account that first published it can submit new versions. Pick a different id for your own theme.",
    "submission-version-not-newer": "The version in theme.json must be greater than the newest published version of this theme.",
    "submission-in-review": "Another submission issue for this theme id is already waiting for review. Update that issue instead, or wait until it is published or closed.",
    "submission-version-limit": "This theme already has 16 published versions, the most Pak Rat keeps for one theme. Ask a maintainer for help.",
}


def explain(slug: str) -> str:
    for table in (SUBMISSION_REASONS, THEME_REASONS, THEME_WARNINGS):
        if slug in table:
            return table[slug]
    return "This check failed."
