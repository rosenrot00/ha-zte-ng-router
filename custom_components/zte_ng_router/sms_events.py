"""Detect newly received SMS messages so they can be fired as Home Assistant events.

Phone numbers and message bodies are excluded by default. Entity previews and
event content require separate opt-ins because Home Assistant may record both.
"""

from __future__ import annotations

from typing import Any

EVENT_SMS_RECEIVED = "zte_ng_router_sms_received"

# ZTE inbox ``tag`` values: 0 = read, 1 = unread. Sent (2), failed (3) and draft
# (4) messages are not incoming and never produce an event.
_INBOUND_TAGS = frozenset({"0", "1"})
# Keep more identities than one inbox response can hold, even across empty or partial reads.
_SEEN_LIMIT = 4096


def _message_key(message: dict[str, Any]) -> str:
    """Return a stable identity for a stored message."""
    return f"{message.get('mem_store')}:{message.get('id')}:{message.get('date')}"


def _is_inbound(message: dict[str, Any]) -> bool:
    return str(message.get("tag")) in _INBOUND_TAGS


def sms_event_data(
    message: dict[str, Any], entry_id: str, name: str, *, include_content: bool,
) -> dict[str, Any]:
    """Build the public event; the recorder may persist its contents."""
    data = {"entry_id": entry_id, "name": name, "id": message.get("id"), "date": message.get("date")}
    if include_content:
        data.update(number=message.get("number"), text=message.get("content_decoded"))
    return data


def collect_new_incoming_sms(
    messages: list[Any] | None,
    seen: dict[str, None] | None,
) -> tuple[list[dict[str, Any]], dict[str, None] | None]:
    """Return newly arrived incoming messages and the updated set of seen keys.

    ``seen`` is ``None`` on the first poll after startup: the existing inbox is
    only recorded, so restarting Home Assistant does not replay old messages.
    """
    if not isinstance(messages, list):
        return [], seen
    first_poll = seen is None
    if seen is None:
        seen = {}
    new = []
    # Oldest first, so automations see messages in arrival order.
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("id") is None:
            continue
        key = _message_key(message)
        if key in seen:
            continue
        seen[key] = None
        if not first_poll and _is_inbound(message):
            new.append(message)
    while len(seen) > _SEEN_LIMIT:
        del seen[next(iter(seen))]
    return new, seen
