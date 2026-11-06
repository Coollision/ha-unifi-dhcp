"""Tests for feeding UniFi clients into DHCP discovery."""

from __future__ import annotations

from unittest.mock import patch

from aiounifi.interfaces.api_handlers import ItemEvent
from homeassistant.components.dhcp.models import DHCPData, DhcpMatchers
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.unifi_dhcp.const import (
    CONF_NETWORKS,
    DOMAIN,
    ISSUE_NETWORKS_MISSING,
    ISSUE_UNIFI_UNAVAILABLE,
    RESYNC_INTERVAL,
)

from .conftest import (
    NETWORK_IOT,
    NETWORK_LAN,
    NETWORK_OTHER,
    make_client,
    set_reachable,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_only_selected_networks_are_fed(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """Clients of selected networks are fed, everything else is ignored."""
    clients = unifi_entry.runtime_data.api.clients
    clients["aa:bb:cc:00:00:01"] = make_client(
        "aa:bb:cc:00:00:01", "192.0.2.10", "plug", NETWORK_IOT
    )
    clients["aa:bb:cc:00:00:02"] = make_client(
        "aa:bb:cc:00:00:02", "192.0.2.20", "phone", NETWORK_LAN
    )
    clients["aa:bb:cc:00:00:03"] = make_client(
        "aa:bb:cc:00:00:03", "", "offline", NETWORK_IOT
    )
    clients["aa:bb:cc:00:00:04"] = make_client(
        "aa:bb:cc:00:00:04", "192.0.2.40", "unassigned", None
    )

    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert dhcp_data.address_data == {
        "aabbcc000001": {"ip": "192.0.2.10", "hostname": "plug"}
    }
    stats = config_entry.runtime_data.stats
    assert (stats.fed, stats.skipped_other_network, stats.skipped_no_ip) == (1, 2, 1)


async def test_matcher_hit_starts_a_discovery_flow(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """A client matching an integration matcher starts that integration's flow."""
    dhcp_data.integration_matchers = DhcpMatchers(
        registered_devices_domains=set(),
        no_oui_matchers={},
        oui_matchers={"AABBCC": [{"domain": "test_domain", "macaddress": "AABBCC*"}]},
    )
    unifi_entry.runtime_data.api.clients["aa:bb:cc:00:00:01"] = make_client(
        "aa:bb:cc:00:00:01", "192.0.2.10", "plug", NETWORK_IOT
    )

    with patch(
        "homeassistant.components.dhcp.discovery_flow.async_create_flow"
    ) as create_flow:
        await _setup(hass, config_entry)

    create_flow.assert_called_once()
    assert create_flow.call_args.args[1] == "test_domain"
    assert create_flow.call_args.args[3].ip == "192.0.2.10"


async def test_client_events_are_fed_live(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """A client that appears after setup is fed without waiting for a resync."""
    await _setup(hass, config_entry)
    assert dhcp_data.address_data == {}

    clients = unifi_entry.runtime_data.api.clients
    clients["aa:bb:cc:00:00:09"] = make_client(
        "aa:bb:cc:00:00:09", "192.0.2.90", "new", NETWORK_IOT
    )
    clients.emit(ItemEvent.ADDED, "aa:bb:cc:00:00:09")
    clients.emit(ItemEvent.ADDED, "aa:bb:cc:00:00:ff")  # unknown client

    assert dhcp_data.address_data == {
        "aabbcc000009": {"ip": "192.0.2.90", "hostname": "new"}
    }


async def test_resync_runs_on_a_timer(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """The periodic pass picks up clients that were never announced by an event."""
    await _setup(hass, config_entry)
    unifi_entry.runtime_data.api.clients["aa:bb:cc:00:00:05"] = make_client(
        "aa:bb:cc:00:00:05", "192.0.2.50", "quiet", NETWORK_IOT
    )

    async_fire_time_changed(hass, dt_util.utcnow() + RESYNC_INTERVAL)
    await hass.async_block_till_done()

    assert "aabbcc000005" in dhcp_data.address_data


async def test_unavailable_logs_once_and_recovers(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An outage is logged once, raises a repair issue and clears on recovery."""
    await _setup(hass, config_entry)
    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_UNIFI_UNAVAILABLE}_{config_entry.entry_id}"

    set_reachable(hass, unifi_entry, False)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + RESYNC_INTERVAL)
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    assert caplog.text.count("UniFi is unavailable") == 1
    assert not config_entry.runtime_data.available

    set_reachable(hass, unifi_entry, True)
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert "UniFi is available again" in caplog.text
    assert config_entry.runtime_data.available


async def test_missing_network_raises_an_issue(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A selected network that UniFi no longer lists is reported once."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_NETWORKS: [NETWORK_IOT, NETWORK_OTHER]}
    )

    await _setup(hass, config_entry)

    issue_id = f"{ISSUE_NETWORKS_MISSING}_{config_entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"networks": NETWORK_OTHER}
    assert caplog.text.count("no longer exist") == 1


async def test_reloaded_unifi_entry_is_rebound(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """After UniFi reloads its hub, the subscription moves to the new hub."""
    await _setup(hass, config_entry)
    old_clients = unifi_entry.runtime_data.api.clients
    assert len(old_clients.callbacks) == 1

    new_hub = type(unifi_entry.runtime_data)(
        available=True,
        signal_reachable="unifi-reachable-new",
        api=type(unifi_entry.runtime_data.api)(
            clients=type(old_clients)(), networks=unifi_entry.runtime_data.api.networks
        ),
    )
    unifi_entry.runtime_data = new_hub

    async_fire_time_changed(hass, dt_util.utcnow() + RESYNC_INTERVAL)
    await hass.async_block_till_done()

    assert old_clients.callbacks == []
    assert len(new_hub.api.clients.callbacks) == 1


async def test_setup_retries_when_unifi_is_not_loaded(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """The entry waits for the UniFi entry instead of failing."""
    unifi_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_fails_when_unifi_entry_is_gone(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """A removed UniFi entry is a configuration error, not a retry."""
    await hass.config_entries.async_remove(unifi_entry.entry_id)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_cleans_up(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """Unloading removes the subscription and any issue the entry raised."""
    await _setup(hass, config_entry)
    set_reachable(hass, unifi_entry, False)
    await hass.async_block_till_done()
    clients = unifi_entry.runtime_data.api.clients

    assert await hass.config_entries.async_unload(config_entry.entry_id)

    assert clients.callbacks == []
    issue_id = f"{ISSUE_UNIFI_UNAVAILABLE}_{config_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_unchanged_clients_are_not_fed_again(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UniFi reports every client every few seconds, but only changes are passed on."""
    clients = unifi_entry.runtime_data.api.clients
    clients["aa:bb:cc:00:00:01"] = make_client(
        "aa:bb:cc:00:00:01", "192.0.2.10", "plug", NETWORK_IOT
    )
    await _setup(hass, config_entry)
    feed = config_entry.runtime_data
    caplog.clear()

    with patch.object(
        type(feed), "async_process_client", wraps=feed.async_process_client
    ) as process:
        for _ in range(5):
            clients.emit(ItemEvent.CHANGED, "aa:bb:cc:00:00:01")
        process.assert_not_called()
        assert caplog.text.count("handed over") == 0

        clients["aa:bb:cc:00:00:01"] = make_client(
            "aa:bb:cc:00:00:01", "192.0.2.11", "plug", NETWORK_IOT
        )
        clients.emit(ItemEvent.CHANGED, "aa:bb:cc:00:00:01")
        process.assert_called_once_with("192.0.2.11", "plug", "aa:bb:cc:00:00:01")

    assert feed.known_clients == 1
    assert feed.stats.events_fed == 1


async def test_pass_counters_are_per_pass(
    hass: HomeAssistant,
    dhcp_data: DHCPData,
    unifi_entry: MockConfigEntry,
    config_entry: MockConfigEntry,
) -> None:
    """A second pass reports the same clients as unchanged, not as fed again."""
    unifi_entry.runtime_data.api.clients["aa:bb:cc:00:00:01"] = make_client(
        "aa:bb:cc:00:00:01", "192.0.2.10", "plug", NETWORK_IOT
    )
    await _setup(hass, config_entry)
    assert config_entry.runtime_data.stats.fed == 1

    async_fire_time_changed(hass, dt_util.utcnow() + RESYNC_INTERVAL)
    await hass.async_block_till_done()

    stats = config_entry.runtime_data.stats
    assert (stats.clients, stats.fed, stats.unchanged) == (1, 0, 1)
