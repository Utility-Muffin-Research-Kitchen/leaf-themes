"""Shared test setup: import paths and small in-memory theme fixtures.

The fixtures are built here from the standard library (a tiny PNG writer and
zipfile) so the tests never depend on files outside this repository, apart
from the leaf-contracts checkout that provides the validator.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import struct
import sys
import zipfile
import zlib

sys.dont_write_bytecode = True
TESTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS)
TOOLS = os.path.join(REPO_ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import form  # noqa: E402


def leaf_docs_dir() -> str:
    return os.path.abspath(os.environ.get("LEAF_DOCS_DIR",
                                          os.path.join(REPO_ROOT, "..", "leaf-docs")))


def png(width: int, height: int, shade: int = 0) -> bytes:
    """A real 8-bit grayscale PNG of one shade."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data))
    row = b"\x00" + bytes([shade]) * width
    raw = row * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def manifest(theme_id="neon-nights", version="1.0.0", **extra) -> dict:
    obj = {"schema": 1, "id": theme_id, "name": "Neon Nights", "author": "Example",
           "version": version, "min_leaf_version": "0.12.0", "license": "CC-BY-4.0",
           "description": "Pink and cyan on black."}
    obj.update(extra)
    return {k: v for k, v in obj.items() if v is not None}


def theme_files(theme_id="neon-nights", version="1.0.0", shade=10, **extra) -> dict:
    root = theme_id if "root" not in extra else extra.pop("root")
    return {
        f"{root}/theme.json": json.dumps(manifest(theme_id, version, **extra),
                                         indent=2).encode(),
        f"{root}/preview.png": png(960, 720, shade),
        f"{root}/grid/icons/FC.png": png(512, 512, shade + 1),
        f"{root}/grid/icons/MD.png": png(256, 256, shade + 2),
    }


def theme_zip(files: dict, *, order=None, dirs=True, date=(2026, 9, 14, 12, 30, 0),
              compression=zipfile.ZIP_DEFLATED) -> bytes:
    """A submitter-style zip: directory entries, a timestamp, any order."""
    buffer = io.BytesIO()
    names = list(order or files)
    with zipfile.ZipFile(buffer, "w", compression) as bundle:
        if dirs:
            seen = set()
            for name in names:
                parts = name.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    folder = "/".join(parts[:i]) + "/"
                    if folder not in seen:
                        seen.add(folder)
                        info = zipfile.ZipInfo(folder, date)
                        info.external_attr = (0o040755 << 16) | 0x10
                        info.create_system = 3
                        bundle.writestr(info, b"")
        for name in names:
            info = zipfile.ZipInfo(name, date)
            info.external_attr = 0o100600 << 16
            info.create_system = 3
            info.compress_type = compression
            bundle.writestr(info, files[name])
    return buffer.getvalue()


def write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def form_body(url="https://github.com/user-attachments/files/123456/neon-nights.zip",
              license_label="CC BY 4.0", summary="Pink and cyan on black.",
              checked=(True, True, True)) -> str:
    boxes = "\n".join(f"- [{'X' if on else ' '}] {text}"
                      for on, text in zip(checked, form.CONFIRMATIONS))
    link = f"[neon-nights.zip]({url})" if url else "_No response_"
    return (f"### {form.ZIP_LABEL}\n\n{link}\n\n"
            f"### {form.LICENSE_LABEL}\n\n{license_label}\n\n"
            f"### {form.SUMMARY_LABEL}\n\n{summary}\n\n"
            f"### {form.CONFIRM_LABEL}\n\n{boxes}\n")


def repo_copy(temp: str, owners=None, versions=None) -> str:
    """A scratch leaf-themes tree with owners.json and themes/<id>/<version>.json."""
    root = os.path.join(temp, "repo")
    os.makedirs(os.path.join(root, "themes"), exist_ok=True)
    with open(os.path.join(root, "owners.json"), "w") as handle:
        json.dump(owners or {}, handle)
    for path, record in (versions or {}).items():
        write(os.path.join(root, path), json.dumps(record).encode())
    return root


def copy_tree(src: str, dst: str) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)
