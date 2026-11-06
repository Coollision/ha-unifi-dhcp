"""Tests that every translation the integration relies on exists in every language."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from custom_components.unifi_dhcp.const import (
    ISSUE_NETWORKS_MISSING,
    ISSUE_UNIFI_UNAVAILABLE,
)

TRANSLATIONS = Path(__file__).parents[1] / "custom_components/unifi_dhcp/translations"
LANGUAGES = sorted(path.stem for path in TRANSLATIONS.glob("*.json"))

# Every key the code or the flows can emit, as a path into the translation file.
REQUIRED = [
    "config.step.user.title",
    "config.step.user.description",
    "config.step.user.data.unifi_entry_id",
    "config.step.user.data_description.unifi_entry_id",
    "config.step.networks.title",
    "config.step.networks.description",
    "config.step.networks.data.networks",
    "config.step.networks.data_description.networks",
    "config.error.no_networks_selected",
    "config.abort.already_configured",
    "config.abort.no_unifi_entry",
    "config.abort.no_networks",
    "options.step.init.title",
    "options.step.init.description",
    "options.step.init.data.networks",
    "options.step.init.data_description.networks",
    "options.error.no_networks_selected",
    "options.abort.no_unifi_entry",
    "exceptions.unifi_entry_missing.message",
    "exceptions.unifi_not_loaded.message",
    "exceptions.no_loaded_entry.message",
    "services.resync.name",
    "services.resync.description",
    "services.resync.fields.force.name",
    "services.resync.fields.force.description",
    f"issues.{ISSUE_UNIFI_UNAVAILABLE}.title",
    f"issues.{ISSUE_UNIFI_UNAVAILABLE}.description",
    f"issues.{ISSUE_NETWORKS_MISSING}.title",
    f"issues.{ISSUE_NETWORKS_MISSING}.description",
]

PLACEHOLDERS = {
    "config.step.networks.description": {"unifi"},
    "options.step.init.description": {"unifi"},
    "exceptions.unifi_not_loaded.message": {"title"},
    f"issues.{ISSUE_UNIFI_UNAVAILABLE}.description": {"title"},
    f"issues.{ISSUE_NETWORKS_MISSING}.description": {"networks"},
}


def _load(language: str) -> dict:
    return json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))


def _leaves(node: dict, prefix: str = "") -> dict[str, str]:
    leaves: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.update(_leaves(value, path))
        else:
            leaves[path] = value
    return leaves


def test_english_exists() -> None:
    """English is the fallback language and must always be there."""
    assert "en" in LANGUAGES


@pytest.mark.parametrize("language", LANGUAGES)
def test_required_keys_exist(language: str) -> None:
    """Every key the integration can emit has a non-empty text."""
    leaves = _leaves(_load(language))
    missing = [key for key in REQUIRED if not leaves.get(key)]
    assert not missing, f"{language} lacks {missing}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_same_keys_as_english(language: str) -> None:
    """A language neither lacks nor adds keys compared with English."""
    assert _leaves(_load(language)).keys() == _leaves(_load("en")).keys()


@pytest.mark.parametrize("language", LANGUAGES)
def test_placeholders_match(language: str) -> None:
    """Texts use exactly the placeholders the code passes in."""
    leaves = _leaves(_load(language))
    for key, expected in PLACEHOLDERS.items():
        assert set(re.findall(r"{(\w+)}", leaves[key])) == expected, key
