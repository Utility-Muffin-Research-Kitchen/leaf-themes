"""THEME-1 validation and the canonical rebuild of a theme zip.

Devices receive exactly the bytes that passed: the rebuilt zip is validated
again, and it is the rebuilt zip's bytes, sha256 and sizes that get staged,
recorded and published. The submitter's own zip is never republished.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
import zlib
from dataclasses import dataclass, field

from contracts import theme_model

# 1980-01-01 00:00:00, the earliest DOS timestamp: fixed so the same files
# always produce the same bytes.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644
DEFLATE_LEVEL = 9

# Reasons after which theme.json cannot be trusted to name the theme, so the
# id-based checks (ownership, version) are skipped rather than guessed.
_UNREADABLE = {
    "theme-archive-too-large", "theme-malformed-archive", "theme-too-many-entries",
    "theme-unsupported-compression", "theme-uncompressed-too-large",
    "theme-compression-ratio", "theme-entry-name-encoding", "theme-absolute-path",
    "theme-backslash-path", "theme-path-traversal", "theme-hidden-file",
    "theme-symlink", "theme-special-file", "theme-duplicate-entry",
    "theme-not-single-folder", "theme-missing-manifest", "theme-manifest-too-large",
    "theme-malformed-manifest", "theme-unknown-schema", "theme-id-invalid",
}


@dataclass
class Inspection:
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict | None = None       # set when theme.json could be read
    # slug -> the zip entries it is about, for reasons and warnings that name
    # one. Entry names come from the submission: render them with code().
    where: dict[str, list[str]] = field(default_factory=dict)


def _read_manifest(path: str):
    tm = theme_model()
    with zipfile.ZipFile(path) as bundle:
        names = [info.filename for info in bundle.infolist()
                 if info.filename.endswith("/theme.json") and info.filename.count("/") == 1]
        if len(names) != 1:
            return None
        info = bundle.getinfo(names[0])
        if info.file_size > tm.MAX_MANIFEST_BYTES:
            return None
        with bundle.open(info) as handle:
            data = handle.read(tm.MAX_MANIFEST_BYTES + 1)
    obj = tm.parse_manifest_bytes(data)
    return obj if isinstance(obj, dict) else None


def inspect(path: str) -> Inspection:
    """Run the reference validator; read theme.json when that is still safe."""
    tm = theme_model()
    findings, warning_findings = tm.validate_archive_findings(path)
    reasons = sorted({slug for slug, _ in findings})
    warnings = sorted({slug for slug, _ in warning_findings})
    result = Inspection(list(reasons), list(warnings))
    for slug, entry in findings + warning_findings:
        if entry is not None:
            result.where.setdefault(slug, []).append(entry)
    if not set(reasons) & _UNREADABLE:
        try:
            result.manifest = _read_manifest(path)
        except (zipfile.BadZipFile, ValueError, RecursionError, OSError, KeyError):
            result.manifest = None
    return result


def _store_instead(data: bytes) -> bool:
    """Deflate unless it does not shrink the file or breaks the 100:1 rule."""
    compressor = zlib.compressobj(DEFLATE_LEVEL, zlib.DEFLATED, -15)
    compressed = len(compressor.compress(data) + compressor.flush())
    return compressed >= len(data) or len(data) > theme_model().MAX_RATIO * compressed


def rebuild(source_path: str) -> bytes:
    """A canonical zip holding the regular files of an already validated zip.

    Entries in sorted name order, no directory entries, fixed timestamps,
    Unix regular-file attributes, deflate at level 9 (stored when deflate does
    not help). The same input always gives the same bytes with the same zlib.
    """
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(source_path) as bundle:
        for info in bundle.infolist():
            if info.filename.endswith("/"):
                continue
            with bundle.open(info) as handle:
                data = handle.read(info.file_size + 1)
            if len(data) != info.file_size:
                raise ValueError(f"{info.filename}: size does not match the zip")
            files[info.filename] = data

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name in sorted(files):
            data = files[name]
            entry = zipfile.ZipInfo(name, FIXED_DATE_TIME)
            entry.create_system = 3
            entry.external_attr = FILE_MODE << 16
            if _store_instead(data):
                entry.compress_type = zipfile.ZIP_STORED
                out.writestr(entry, data)
            else:
                entry.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(entry, data, compresslevel=DEFLATE_LEVEL)
    return buffer.getvalue()


def installed_size(zip_bytes: bytes) -> int:
    """Sum of the uncompressed sizes of every entry."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as bundle:
        return sum(info.file_size for info in bundle.infolist())


def read_member(zip_bytes: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as bundle:
        return bundle.read(name)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
