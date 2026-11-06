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


async def test_abort_without_unifi(hass: HomeAssistant) -> None:
    """Without a loaded UniFi entry the flow cannot continue."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_unifi_entry"


async def test_full_flow_with_no_preselected_networks(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """Networks must be chosen explicitly and nothing is pre-selected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIFI_ENTRY_ID: UNIFI_ENTRY_ID}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "networks"
    schema = result["data_schema"].schema
    marker = next(key for key in schema if key == CONF_NETWORKS)
    assert marker.default is vol.UNDEFINED  # nothing is pre-selected
    options = schema[marker].config["options"]
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

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIFI_ENTRY_ID: UNIFI_ENTRY_ID}
    )

    schema = result["data_schema"].schema
    options = schema[next(key for key in schema if key == CONF_NETWORKS)].config[
        "options"
    ]
    assert options == [{"value": NETWORK_IOT, "label": "IoT"}]


async def test_abort_when_no_networks_at_all(
    hass: HomeAssistant, unifi_entry: MockConfigEntry
) -> None:
    """Without any network to choose from the flow stops."""
    unifi_entry.runtime_data.api.networks.clear()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIFI_ENTRY_ID: UNIFI_ENTRY_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_networks"


async def test_one_entry_per_unifi_entry(
    hass: HomeAssistant, unifi_entry: MockConfigEntry, config_entry: MockConfigEntry
) -> None:
    """The same UniFi entry cannot be linked twice."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UNIFI_ENTRY_ID: UNIFI_ENTRY_ID}
    )

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
