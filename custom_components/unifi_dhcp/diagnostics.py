"""Diagnostics for the UniFi DHCP enrichment integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CLIENT_COOLDOWN,
    CONF_UNIFI_ENTRY_ID,
    HANDOVERS_PER_SECOND,
    MAX_PENDING,
)

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
        "clients_handed_over": feed.known_clients,
        "handovers_total": feed.handed_over,
        "queue_length": feed.queue_length,
        "limits": {
            "handovers_per_second": HANDOVERS_PER_SECOND,
            "max_pending": MAX_PENDING,
            "client_cooldown_seconds": CLIENT_COOLDOWN.total_seconds(),
        },
        "last_pass": {
            "at": stats.last_resync.isoformat() if stats.last_resync else None,
            "clients": stats.clients,
            "queued": stats.queued,
            "unchanged": stats.unchanged,
            "skipped_other_network": stats.skipped_other_network,
            "skipped_no_ipv4": stats.skipped_no_ipv4,
            "skipped_queue_full": stats.skipped_queue_full,
        },
        "events_queued_since_last_pass": stats.events_queued,
    }
