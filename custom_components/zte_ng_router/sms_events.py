"""Detect newly received SMS messages so they can be fired as Home Assistant events.

Phone numbers and message bodies are deliberately *not* exposed through entity
state or attributes: both end up in the recorder and are readable by every user
of the instance. Incoming messages are announced once, as an event, instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

EVENT_SMS_RECEIVED = "zte_ng_router_sms_received"

# ZTE inbox ``tag`` values: 0 = read, 1 = unread. Sent (2), failed (3) and draft
# (4) messages are not incoming and never produce an event.
_INBOUND_TAGS = frozenset({"0", "1"})


def _message_key(message: dict[str, Any]) -> str:
    """Return a stable identity for a stored message."""
    return f"{message.get('mem_store')}:{message.get('id')}:{message.get('date')}"


def _is_inbound(message: dict[str, Any]) -> bool:
    return str(message.get("tag")) in _INBOUND_TAGS


def collect_new_incoming_sms(
    messages: Iterable[Any] | None,
    seen: set[str] | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Return newly arrived incoming messages and the updated set of seen keys.

    ``seen`` is ``None`` on the first poll after startup: the existing inbox is
    only recorded, so restarting Home Assistant does not replay old messages.
    """
    current = [m for m in (messages or []) if isinstance(m, dict) and m.get("id") is not None]
    keys = {_message_key(m) for m in current}
    if seen is None:
        return [], keys
    new = [m for m in current if _is_inbound(m) and _message_key(m) not in seen]
    # Oldest first, so automations see messages in arrival order.
    new.reverse()
    return new, keys
