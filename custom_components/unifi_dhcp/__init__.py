"""Enrich Home Assistant's DHCP discovery with UniFi clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.unifi import DOMAIN as UNIFI_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .const import CONF_UNIFI_ENTRY_ID, DOMAIN
from .feed import UnifiDhcpFeed

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

type UnifiDhcpConfigEntry = ConfigEntry[UnifiDhcpFeed]


async def async_setup_entry(hass: HomeAssistant, entry: UnifiDhcpConfigEntry) -> bool:
    """Set up the feed for one UniFi config entry."""
    unifi_entry = hass.config_entries.async_get_entry(entry.data[CONF_UNIFI_ENTRY_ID])
    if unifi_entry is None or unifi_entry.domain != UNIFI_DOMAIN:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="unifi_entry_missing"
        )
    if unifi_entry.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="unifi_not_loaded",
            translation_placeholders={"title": unifi_entry.title},
        )

    feed = UnifiDhcpFeed(hass, entry, unifi_entry)
    entry.runtime_data = feed
    entry.async_on_unload(feed.async_stop)
    await feed.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UnifiDhcpConfigEntry) -> bool:
    """Unload a config entry."""
    return True
