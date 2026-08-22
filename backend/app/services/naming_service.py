"""Filenames for the exported STL files.

Requirements the naming has to satisfy at once:

* predictable and sortable:      ``3001_Brick_2x4_01.stl``
* unambiguous per instance:      ``_01``, ``_02``, ... per part
* safe on Windows, macOS, Linux: no ``/ \\ : * ? " < > |`` and no reserved names
* short enough to survive deep extraction paths
"""
from __future__ import annotations

import re

from .normalize import sanitize_filename

#: Trim the verbose catalogue names down to something a filename can carry.
_NOISE_WORDS = re.compile(
    r"\b(with|and|the|type|version|style|for|pattern|print)\b", re.IGNORECASE)
MAX_NAME_PART = 40


def short_part_name(name: str) -> str:
    """Condense a catalogue part name for use inside a filename."""
    text = (name or "").strip()
    if not text:
        return "Part"
    # Catalogue names often carry a long qualifier after a comma or dash.
    text = re.split(r"[,(]", text)[0]
    text = _NOISE_WORDS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_NAME_PART:
        text = text[:MAX_NAME_PART].rsplit(" ", 1)[0] or text[:MAX_NAME_PART]
    return sanitize_filename(text, max_length=MAX_NAME_PART, fallback="Part")


def instance_filename(part_num: str, name: str, index: int, total: int,
                      extension: str = "stl") -> str:
    """Filename for one physical copy of a part.

    ``index`` is 1-based.  The instance suffix is zero-padded to the width of
    ``total`` so files sort correctly in every file manager.
    """
    part_id = sanitize_filename(part_num, max_length=24, fallback="part")
    label = short_part_name(name)
    width = max(2, len(str(total)))
    return f"{part_id}_{label}_{index:0{width}d}.{extension}"


def zip_basename(set_number: str) -> str:
    """``77263`` -> ``LEGO_77263_Print_Pack``."""
    safe = sanitize_filename(set_number, max_length=24, fallback="set")
    return f"LEGO_{safe}_Print_Pack"
