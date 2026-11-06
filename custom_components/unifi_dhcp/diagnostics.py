"""Diagnostics for the UniFi DHCP enrichment integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_UNIFI_ENTRY_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import UnifiDhcpConfigEntry

TO_REDACT = {CONF_UNIFI_ENTRY_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiDhcpConfigEntry
) -> dict[str, Any]:
    """Return diagnostics. No client data (MAC, IP, hostname) is included."""
    feed = entry.runtime_data
    stats = feed.stats
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "unifi_available": feed.available,
        "selected_networks": sorted(feed.networks),
        "last_pass": {
            "at": stats.last_resync.isoformat() if stats.last_resync else None,
            "fed": stats.fed,
            "skipped_other_network": stats.skipped_other_network,
            "skipped_no_ip": stats.skipped_no_ip,
        },
    }
