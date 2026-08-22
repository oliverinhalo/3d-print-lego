"""Set-number normalisation and input validation.

Users type set numbers in many shapes.  All of these must resolve to the
same set::

    77263      #77263      LEGO 77263      lego #77263
    77263-1    77263 - 1   LEGO Set 77263

Rebrickable identifies a set by ``<number>-<inventory version>``, e.g.
``77263-1``, so a bare number is suffixed with ``-1`` by default.
"""
from __future__ import annotations

import re

#: Words users prefix that carry no information.
_NOISE = re.compile(
    r"\b(lego|set|number|no|nr|item|kit|brick|bricks)\b\.?",
    re.IGNORECASE,
)
_ALLOWED = re.compile(r"^[0-9a-zA-Z]+(?:-[0-9]{1,3})?$")

MAX_INPUT_LENGTH = 64


class InvalidSetNumber(ValueError):
    """Raised when user input cannot be read as a LEGO set number."""


def normalize_set_number(raw: str) -> str:
    """Return a canonical ``<number>-<version>`` set id.

    >>> normalize_set_number("#77263")
    '77263-1'
    >>> normalize_set_number("LEGO 77263")
    '77263-1'
    >>> normalize_set_number("77263-2")
    '77263-2'
    """
    if raw is None:
        raise InvalidSetNumber("No set number was provided.")
    text = str(raw).strip()
    if not text:
        raise InvalidSetNumber("No set number was provided.")
    if len(text) > MAX_INPUT_LENGTH:
        raise InvalidSetNumber("That set number is too long.")

    text = _NOISE.sub(" ", text)
    text = text.replace("#", " ").replace("_", "-")
    # Collapse spaces around a version dash: "77263 - 1" -> "77263-1"
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text).strip()

    # If noise removal left several tokens, the set number is the one with digits.
    if " " in text:
        tokens = [t for t in text.split(" ") if any(ch.isdigit() for ch in t)]
        if len(tokens) != 1:
            raise InvalidSetNumber(
                f"Could not read a LEGO set number from {raw!r}. Try something like 77263.")
        text = tokens[0]

    text = text.strip("-").strip()
    if not text or not any(ch.isdigit() for ch in text):
        raise InvalidSetNumber(
            f"Could not read a LEGO set number from {raw!r}. Try something like 77263.")
    if not _ALLOWED.match(text):
        raise InvalidSetNumber(
            f"{raw!r} contains characters that are not part of a set number.")

    if "-" not in text:
        text = f"{text}-1"
    number, _, version = text.partition("-")
    if not version.isdigit() or int(version) < 1:
        raise InvalidSetNumber(f"{raw!r} has an invalid inventory version.")
    return f"{number}-{int(version)}"


def display_number(set_num: str) -> str:
    """"77263-1" -> "77263" for display."""
    return set_num.split("-", 1)[0]


#: Characters forbidden in filenames on Windows, macOS or Linux.
_UNSAFE_FILENAME = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, *, max_length: int = 64, fallback: str = "part") -> str:
    """Make ``name`` safe as a single path component on all platforms.

    Strips directory separators and reserved characters, collapses runs of
    whitespace to single underscores, and refuses to produce a name that
    Windows treats as a device.
    """
    text = str(name or "").strip()
    text = _UNSAFE_FILENAME.sub("", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_{2,}", "_", text)
    text = text.strip("._ ")
    if not text:
        return fallback
    if len(text) > max_length:
        text = text[:max_length].rstrip("._ ") or fallback
    if text.upper().split(".")[0] in _WINDOWS_RESERVED:
        text = f"{text}_"
    return text
