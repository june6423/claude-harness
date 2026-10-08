"""Cross-session message header and inbound-message parsing."""

from __future__ import annotations

import html
import re
from typing import Any

from .records import RecordError, parse_ref

HEADER_LINE = "[HMSG v1]"
MESSAGE_TYPES = ("HANDOFF_READY", "REPORT_READY", "REVIEW_REQUEST", "VERDICT_READY",
                 "QUESTION", "ANSWER", "STATUS")
INBOUND_RE = re.compile(r"<cross-session-message(?P<attrs>[^>]*)>\n?(?P<body>.*?)\n?</cross-session-message>",
                        re.DOTALL)
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def parse_header(text: str) -> dict[str, Any]:
    """Parse an [HMSG v1] message. Return type, refs, and body."""
    lines = text.strip("\n").split("\n")
    if not lines or lines[0].strip() != HEADER_LINE:
        raise RecordError(f"message must start with {HEADER_LINE}")
    try:
        end = [line.strip() for line in lines].index("---", 1)
    except ValueError:
        raise RecordError("message header has no '---' line") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep:
            raise RecordError(f"header line has no ':': {line!r}")
        fields[key.strip()] = value.strip()
    errors = []
    if set(fields) != {"type", "refs"}:
        errors.append("header needs exactly the fields 'type' and 'refs'")
    if fields.get("type") not in MESSAGE_TYPES:
        errors.append(f"type must be one of {', '.join(MESSAGE_TYPES)}")
    refs: list[str] = []
    if fields.get("refs", "NONE") != "NONE":
        for ref in (r.strip() for r in fields["refs"].split(",")):
            try:
                parse_ref(ref)
                refs.append(ref)
            except RecordError as error:
                errors.extend(error.errors)
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        errors.append("message body must not be empty")
    if errors:
        raise RecordError(errors)
    return {"type": fields["type"], "refs": refs, "body": body}


def parse_inbound(prompt: str) -> list[dict[str, str]]:
    """Find cross-session messages that Claude Code delivered as a prompt."""
    found = []
    for match in INBOUND_RE.finditer(prompt):
        attrs = {k: html.unescape(v) for k, v in ATTR_RE.findall(match["attrs"])}
        found.append({"from": attrs.get("from", ""), "from_name": attrs.get("from-name", ""),
                      "text": match["body"]})
    return found


def format_message(message_type: str, refs: list[str], body: str) -> str:
    """Build a message text. The parser accepts the result."""
    text = f"{HEADER_LINE}\ntype: {message_type}\nrefs: {', '.join(refs) or 'NONE'}\n---\n{body.strip()}\n"
    parse_header(text)
    return text
