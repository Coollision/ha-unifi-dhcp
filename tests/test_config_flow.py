"""Tests for the config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.unifi_dhcp.const import (
    CONF_NETWORKS,
    CONF_UNIFI_ENTRY_ID,
    DOMAIN,
)

from .conftest import (
    NETWORK_IOT,
    NETWORK_LAN,
    UNIFI_ENTRY_ID,
    make_client,
)


def _network_options(result: dict) -> list[dict]:
    """Return the options of the networks multi select of a form result."""
    schema = result["data_schema"].schema
    return schema[next(key for key in schema if key == CONF_NETWORKS)].config["options"]


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def test_abort_without_unifi(hass: HomeAssistant) -> None:
    """Without a loaded UniFi entry the flow cannot continue."""
    result = await _start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_unifi_entry"


async def test_single_unifi_entry_skips_the_picker(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """With one UniFi entry the flow goes straight to the networks."""
    result = await _start(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "networks"
    assert result["description_placeholders"] == {"unifi": "UniFi"}


async def test_several_unifi_entries_are_offered_as_a_dropdown(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """With more than one UniFi entry the user picks which one to read."""
    other = MockConfigEntry(
        domain="unifi", entry_id="other", title="Site B", data={"host": "192.0.2.7"}
    )
    other.add_to_hass(hass)
    other.mock_state(hass, ConfigEntryState.LOADED)
    other.runtime_data = unifi_entry.runtime_data

    result = await _start(hass)

    assert result["step_id"] == "user"
    schema = result["data_schema"].schema
    entries = schema[next(key for key in schema if key == CONF_UNIFI_ENTRY_ID)]
    assert {option["label"] for option in entries.config["options"]} == {
        "UniFi",
        "Site B (192.0.2.7)",
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIFI_ENTRY_ID: "other"}
    )
    assert result["step_id"] == "networks"


async def test_full_flow_with_no_preselected_networks(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """Networks must be chosen explicitly and nothing is pre-selected."""
    result = await _start(hass)
    assert result["step_id"] == "networks"

    schema = result["data_schema"].schema
    marker = next(key for key in schema if key == CONF_NETWORKS)
    assert marker.default is vol.UNDEFINED  # nothing is pre-selected
    options = _network_options(result)
    assert {option["value"] for option in options} == {NETWORK_LAN, NETWORK_IOT}
    assert {option["label"] for option in options} == {"LAN (VLAN 5)", "IoT (VLAN 40)"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NETWORKS: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_networks_selected"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NETWORKS: [NETWORK_IOT]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "UniFi"
    assert result["data"] == {CONF_UNIFI_ENTRY_ID: UNIFI_ENTRY_ID}
    assert result["options"] == {CONF_NETWORKS: [NETWORK_IOT]}


async def test_networks_fall_back_to_client_networks(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """If the controller does not list networks, the clients' networks are used."""
    api = unifi_entry.runtime_data.api
    api.networks.clear()
    api.clients["aa:bb:cc:00:00:01"] = make_client("aa:bb:cc:00:00:01", "192.0.2.1")
    api.clients["aa:bb:cc:00:00:01"].raw["network"] = "IoT"

    result = await _start(hass)

    assert _network_options(result) == [{"value": NETWORK_IOT, "label": "IoT"}]


async def test_abort_when_no_networks_at_all(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """Without any network to choose from the flow stops."""
    unifi_entry.runtime_data.api.networks.clear()

    result = await _start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_networks"


async def test_one_entry_per_unifi_entry(
    hass: HomeAssistant, unifi_entry: MockConfigEntry, config_entry: MockConfigEntry
) -> None:
    """The same UniFi entry cannot be linked twice."""
    result = await _start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("dhcp_data")
async def test_options_flow_changes_networks_and_reloads(
    hass: HomeAssistant, unifi_entry: MockConfigEntry, config_entry: MockConfigEntry
) -> None:
    """Changing the selection stores it and reloads the entry."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    first_feed = config_entry.runtime_data

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NETWORKS: []}
    )
    assert result["errors"] == {"base": "no_networks_selected"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NETWORKS: [NETWORK_LAN, NETWORK_IOT]}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_NETWORKS: [NETWORK_LAN, NETWORK_IOT]}
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data is not first_feed
    assert config_entry.runtime_data.networks == {NETWORK_LAN, NETWORK_IOT}
