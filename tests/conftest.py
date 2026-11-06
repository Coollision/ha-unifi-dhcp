"""Fixtures for the UniFi DHCP enrichment tests."""

from __future__ import annotations

from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiounifi.interfaces.api_handlers import ItemEvent
from homeassistant.components.dhcp.models import DATA_DHCP, DHCPData, DhcpMatchers
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_dhcp.const import (
    CONF_NETWORKS,
    CONF_UNIFI_ENTRY_ID,
    DOMAIN,
)

NETWORK_LAN = "net-lan"
NETWORK_IOT = "net-iot"
NETWORK_OTHER = "net-other"

UNIFI_ENTRY_ID = "unifi-entry-id"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading the integration from custom_components."""


def make_client(
    mac: str,
    ip: str = "",
    hostname: str = "",
    network_id: str | None = NETWORK_IOT,
) -> SimpleNamespace:
    """Return an object shaped like an aiounifi client."""
    raw: dict[str, Any] = {"mac": mac, "ip": ip, "hostname": hostname}
    if network_id is not None:
        raw["network_id"] = network_id
    return SimpleNamespace(mac=mac, ip=ip, hostname=hostname, raw=raw)


def make_network(
    network_id: str, name: str, vlan: int | None = None
) -> SimpleNamespace:
    """Return an object shaped like an aiounifi network."""
    raw: dict[str, Any] = {"_id": network_id, "name": name}
    if vlan is not None:
        raw["vlan"] = vlan
    return SimpleNamespace(id=network_id, name=name, is_wan=False, raw=raw)


class FakeClients(dict):
    """A client table that records the subscription like aiounifi does."""

    def __init__(self) -> None:
        """Initialize."""
        super().__init__()
        self.callbacks: list[Callable[[ItemEvent, str], None]] = []
        self.unsubscribed = 0

    def subscribe(
        self, callback: Callable[[ItemEvent, str], None], event_filter: Any = None
    ) -> Callable[[], None]:
        """Record a subscription."""
        self.callbacks.append(callback)

        def _unsub() -> None:
            self.callbacks.remove(callback)
            self.unsubscribed += 1

        return _unsub

    def emit(self, event: ItemEvent, mac: str) -> None:
        """Deliver an event to the subscribers."""
        for callback in list(self.callbacks):
            callback(event, mac)


class FakeNetworks(dict):
    """A network table with the ``update`` coroutine aiounifi handlers have."""

    def __init__(self, networks: dict[str, Any] | None = None) -> None:
        """Initialize."""
        super().__init__(networks or {})
        self.update_mock = AsyncMock()

    async def update(self) -> None:  # type: ignore[override]
        """Pretend to refresh from the controller."""
        await self.update_mock()


@pytest.fixture
def dhcp_data(hass: HomeAssistant) -> DHCPData:
    """Provide the data the dhcp integration normally sets up."""
    data = DHCPData(
        integration_matchers=DhcpMatchers(
            registered_devices_domains=set(), no_oui_matchers={}, oui_matchers={}
        )
    )
    hass.data[DATA_DHCP] = data
    return data


@pytest.fixture(autouse=True)
def mock_dependencies() -> Generator[None]:
    """Do not start the real dhcp sniffer or UniFi when dependencies are set up."""
    with (
        patch("homeassistant.components.dhcp.async_setup", return_value=True),
        patch("homeassistant.components.unifi.async_setup", return_value=True),
        patch(
            "homeassistant.components.unifi_discovery.async_setup", return_value=True
        ),
    ):
        yield


@pytest.fixture
def unifi_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Provide a loaded UniFi config entry with a fake hub."""
    hub = SimpleNamespace(
        available=True,
        signal_reachable="unifi-reachable-test",
        async_reset=AsyncMock(return_value=True),
        api=SimpleNamespace(
            clients=FakeClients(),
            networks=FakeNetworks(
                {
                    NETWORK_LAN: make_network(NETWORK_LAN, "LAN", 5),
                    NETWORK_IOT: make_network(NETWORK_IOT, "IoT", 40),
                }
            ),
        ),
    )
    entry = MockConfigEntry(
        domain="unifi", entry_id=UNIFI_ENTRY_ID, title="UniFi", data={}
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = hub
    return entry


@pytest.fixture
def config_entry(hass: HomeAssistant, unifi_entry: MockConfigEntry) -> MockConfigEntry:
    """Provide a config entry for the integration with the IoT network selected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi",
        unique_id=UNIFI_ENTRY_ID,
        data={CONF_UNIFI_ENTRY_ID: UNIFI_ENTRY_ID},
        options={CONF_NETWORKS: [NETWORK_IOT]},
    )
    entry.add_to_hass(hass)
    return entry


def set_reachable(hass: HomeAssistant, entry: MockConfigEntry, available: bool) -> None:
    """Flip the fake hub's availability and signal it like UniFi does."""
    entry.runtime_data.available = available
    async_dispatcher_send(hass, entry.runtime_data.signal_reachable)


__all__ = [
    "NETWORK_IOT",
    "NETWORK_LAN",
    "NETWORK_OTHER",
    "UNIFI_ENTRY_ID",
    "MagicMock",
    "make_client",
    "make_network",
    "set_reachable",
]
