"""Translate failed router actions into errors visible in Home Assistant."""

from collections.abc import Awaitable

from aiohttp import ClientError
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


async def async_run_router_action(
    action: Awaitable[bool],
    *,
    error_key: str,
    placeholders: dict[str, str] | None = None,
) -> None:
    """Execute once, reporting rejection or transport failure without a retry."""
    try:
        succeeded = await action
    except (ClientError, OSError, TimeoutError, RuntimeError) as exc:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=error_key,
            translation_placeholders=placeholders,
        ) from exc
    if not succeeded:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=error_key,
            translation_placeholders=placeholders,
        )
