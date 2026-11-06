"""End to end: a client that moves in UniFi is followed by the integration that owns it.

Uses the real dhcp matching, device registry and config flow manager. Only the
appliance integration is a stand-in; it does what integrations with a
``registered_devices`` DHCP matcher do: follow a known MAC to its new address.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from aiounifi.interfaces.api_handlers import ItemEvent
from homeassistant.components.dhcp.models import DHCPData, DhcpMatchers
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.unifi_dhcp.const import CLIENT_COOLDOWN

from .conftest import NETWORK_IOT, NETWORK_LAN, make_client

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

APPLIANCE = "fake_appliance"
MAC = "bc:10:2f:14:c3:25"
OLD_IP = "192.0.2.50"
NEW_IP = "192.0.2.77"


class FakeApplianceFlow(ConfigFlow, domain=APPLIANCE):
    """Follow a configured appliance whose DHCP lease moved."""

    VERSION = 1

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Update the stored host of the entry that owns this MAC."""
        mac = format_mac(discovery_info.macaddress)
        entry = next(
            other
            for other in self.hass.config_entries.async_entries(APPLIANCE)
            if format_mac(other.data["mac"]) == mac
        )
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, "host": discovery_info.ip}
        )
        return self.async_abort(reason="already_configured")


async def _appliance(hass: HomeAssistant, dhcp_data: DHCPData) -> MockConfigEntry:
    """Set up a known appliance: an entry, and a device that carries its MAC."""
    mock_integration(
        hass, MockModule(APPLIANCE, partial_manifest={"config_flow": True})
    )
    mock_platform(hass, f"{APPLIANCE}.config_flow", None)
    dhcp_data.integration_matchers = DhcpMatchers(
        registered_devices_domains={APPLIANCE}, no_oui_matchers={}, oui_matchers={}
    )
    entry = MockConfigEntry(
        domain=APPLIANCE, title="Fridge", data={"mac": MAC, "host": OLD_IP}
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, MAC)},
    )
    return entry


async def test_a_moved_lease_is_followed(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """UniFi reports a new address for a known appliance; its entry follows."""
    appliance = await _appliance(hass, dhcp_data)
    clients = unifi_entry.runtime_data.api.clients
    clients[MAC] = make_client(MAC, OLD_IP, "fridge", NETWORK_IOT)

    with mock_config_flow(APPLIANCE, FakeApplianceFlow):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert appliance.data["host"] == OLD_IP  # nothing moved yet

        # The lease moves. UniFi reports the new address...
        clients[MAC] = make_client(MAC, NEW_IP, "fridge", NETWORK_IOT)
        clients.emit(ItemEvent.CHANGED, MAC)
        assert appliance.data["host"] == OLD_IP  # ...still cooling down...

        # ...and it is followed as soon as the client's cooldown has passed.
        freezer.tick(CLIENT_COOLDOWN + timedelta(seconds=2))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    assert appliance.data["host"] == NEW_IP


async def test_a_moved_lease_outside_the_selected_networks_is_ignored(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Only the networks you chose are used as a source."""
    appliance = await _appliance(hass, dhcp_data)
    clients = unifi_entry.runtime_data.api.clients
    clients[MAC] = make_client(MAC, OLD_IP, "fridge", NETWORK_LAN)  # not selected

    with mock_config_flow(APPLIANCE, FakeApplianceFlow):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        clients[MAC] = make_client(MAC, NEW_IP, "fridge", NETWORK_LAN)
        clients.emit(ItemEvent.CHANGED, MAC)
        freezer.tick(CLIENT_COOLDOWN + timedelta(seconds=2))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    assert appliance.data["host"] == OLD_IP
