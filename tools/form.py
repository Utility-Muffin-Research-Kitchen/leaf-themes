"""Parse a theme submission issue body.

GitHub renders an issue form as markdown: one `### <label>` heading per field,
then the value, with `_No response_` for an empty optional field and
`- [X] <label>` for a checked box. The submitter can edit that text freely
after opening the issue, so nothing here trusts its shape: a known heading
that appears twice is refused, and every value is bounded and checked.

Keep the labels below in step with .github/ISSUE_TEMPLATE/submit-theme.yml.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

ZIP_LABEL = "Theme zip"
LICENSE_LABEL = "License"
SUMMARY_LABEL = "Summary"
CONFIRM_LABEL = "Confirmations"
KNOWN_LABELS = (ZIP_LABEL, LICENSE_LABEL, SUMMARY_LABEL, CONFIRM_LABEL)

# Form dropdown label -> THEME-1 / catalog license value.
LICENSE_CHOICES = {
    "CC BY 4.0": "CC-BY-4.0",
    "CC BY-SA 4.0": "CC-BY-SA-4.0",
    "CC0": "CC0-1.0",
    "All rights reserved, redistribution permitted": "redistribution-permitted",
}
LICENSE_LABELS = {value: label for label, value in LICENSE_CHOICES.items()}

CONFIRMATIONS = (
    "I made this theme, or I have the right to share every image in it.",
    "I agree that it may be redistributed under the license I chose.",
    "I read the theme rules in the README.",
)

MAX_BODY_CHARS = 65536          # GitHub's own issue body limit
SUMMARY_MAX_CHARS = 100

ATTACHMENT_HOST = "github.com"
# A non-image file uploaded to an issue or an issue form upload field.
ATTACHMENT_PATH_RE = re.compile(r"/user-attachments/files/[0-9]{1,20}/[^/?#]{1,255}")
URL_CANDIDATE_RE = re.compile(r"https?://[^\s<>\[\]()\"'`]+", re.IGNORECASE)
HEADING_RE = re.compile(r"^###[ \t]+(.+?)[ \t]*$")
CHECKED_RE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+(.+?)\s*$")
NO_RESPONSE = "_No response_"

CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Text-direction overrides and isolates: they make a comment or a store
# listing display something other than what it contains.
BIDI_RE = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


@dataclass
class Submission:
    attachment_url: str | None = None
    license: str | None = None
    license_label: str | None = None
    summary: str | None = None
    reasons: list[str] = field(default_factory=list)


def is_attachment_url(url: str) -> bool:
    """True only for https://github.com/user-attachments/files/<n>/<name>.zip."""
    if not isinstance(url, str) or len(url) > 512:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.netloc != ATTACHMENT_HOST:
        # netloc, not hostname: refuses user info, ports and upper case.
        return False
    if parts.query or parts.fragment or "\\" in url:
        return False
    if not ATTACHMENT_PATH_RE.fullmatch(parts.path):
        return False
    name = unquote(parts.path.rsplit("/", 1)[1])
    if "/" in name or "\\" in name or CONTROL_RE.search(name) or name in (".", ".."):
        return False
    return name.lower().endswith(".zip")


def split_sections(body: str) -> tuple[dict[str, str], bool]:
    """({label: text}, malformed). Unknown headings end the previous section."""
    sections: dict[str, list[str]] = {}
    malformed = False
    current = None
    for line in body.split("\n"):
        match = HEADING_RE.match(line)
        if match:
            label = match.group(1)
            if label in KNOWN_LABELS:
                if label in sections:
                    malformed = True
                sections[label] = []
                current = label
            else:
                current = None
            continue
        if current is not None:
            sections[current].append(line)
    return {label: "\n".join(lines).strip() for label, lines in sections.items()}, malformed


def _value(text: str | None) -> str:
    if text is None or text.strip() == NO_RESPONSE:
        return ""
    return text.strip()


def summary_ok(summary: str) -> bool:
    if not summary or len(summary) > SUMMARY_MAX_CHARS or not summary.strip():
        return False
    if CONTROL_RE.search(summary) or BIDI_RE.search(summary):
        return False
    # Unassigned, private-use and surrogate code points have no business here.
    return not any(unicodedata.category(ch) in ("Cn", "Co", "Cs") for ch in summary)


def parse(body) -> Submission:
    result = Submission()
    if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
        result.reasons.append("submission-form-malformed")
        return result
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    sections, malformed = split_sections(body)
    if malformed or any(label not in sections for label in KNOWN_LABELS):
        result.reasons.append("submission-form-malformed")

    # The zip.
    zip_text = _value(sections.get(ZIP_LABEL))
    candidates = URL_CANDIDATE_RE.findall(zip_text)
    allowed = []
    for candidate in candidates:
        if is_attachment_url(candidate) and candidate not in allowed:
            allowed.append(candidate)
    if len(allowed) == 1:
        result.attachment_url = allowed[0]
    elif len(allowed) > 1:
        result.reasons.append("submission-multiple-attachments")
    elif candidates:
        result.reasons.append("submission-attachment-url")
    else:
        result.reasons.append("submission-missing-attachment")

    # The license.
    license_label = _value(sections.get(LICENSE_LABEL))
    if license_label in LICENSE_CHOICES:
        result.license_label = license_label
        result.license = LICENSE_CHOICES[license_label]
    else:
        result.reasons.append("submission-license-missing")

    # The summary: one line, bounded.
    summary = _value(sections.get(SUMMARY_LABEL))
    if summary_ok(summary):
        result.summary = summary.strip()
    else:
        result.reasons.append("submission-summary-invalid")

    # The confirmations.
    checked = set()
    for line in _value(sections.get(CONFIRM_LABEL)).split("\n"):
        match = CHECKED_RE.match(line)
        if match:
            checked.add(match.group(1))
    if not all(item in checked for item in CONFIRMATIONS):
        result.reasons.append("submission-confirmations")
    return result
