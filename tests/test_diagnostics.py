"""Tests for diagnostics."""

from __future__ import annotations

from homeassistant.components.dhcp.models import DHCPData
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_dhcp.const import CONF_UNIFI_ENTRY_ID, DOMAIN
from custom_components.unifi_dhcp.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import NETWORK_IOT, make_client


async def test_diagnostics_has_counts_but_no_client_data(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics report what happened, never which devices."""
    unifi_entry.runtime_data.api.clients["aa:bb:cc:00:00:01"] = make_client(
        "aa:bb:cc:00:00:01", "192.0.2.10", "secret-hostname", NETWORK_IOT
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert result["entry"]["data"][CONF_UNIFI_ENTRY_ID] == REDACTED
    assert result["entry"]["domain"] == DOMAIN
    assert result["selected_networks"] == [NETWORK_IOT]
    assert result["unifi_available"] is True
    assert result["last_pass"]["fed"] == 1
    text = str(result)
    for secret in ("aa:bb:cc:00:00:01", "192.0.2.10", "secret-hostname"):
        assert secret not in text
