"""Unit tests for the region service wiring (Phase 5 rollout 1, item 4)."""

from __future__ import annotations

import importlib

import pytest

import src.services.region_service as region_service


@pytest.fixture
def cfg(monkeypatch):
    """Patch Config on the region_service module and return it for tweaking."""
    from src.config import Config

    # Default to a clean single-region 'primary' state.
    monkeypatch.setattr(Config, "GATEWAY_REGION", "primary", raising=False)
    monkeypatch.setattr(Config, "GATEWAY_REGIONS", "", raising=False)
    monkeypatch.setattr(Config, "GATEWAY_HOME_REGION", "primary", raising=False)
    monkeypatch.setattr(Config, "MULTI_REGION_ENABLED", False, raising=False)
    return Config


def test_single_region_when_multi_disabled(cfg):
    inv = region_service.region_inventory()
    assert [r.name for r in inv] == ["primary"]
    assert region_service.selected_region().name == "primary"


def test_multi_region_ignored_when_flag_off(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "GATEWAY_REGIONS", "us-east:10,eu-west:80", raising=False)
    # flag still false → inventory stays single region
    assert [r.name for r in region_service.region_inventory()] == ["primary"]


def test_multi_region_inventory_parsed_when_enabled(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "MULTI_REGION_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGION", "us-east", raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_HOME_REGION", "us-east", raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGIONS", "us-east:10,eu-west:80", raising=False)
    names = [r.name for r in region_service.region_inventory()]
    assert set(names) == {"us-east", "eu-west"}


def test_home_region_preferred_in_selection(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "MULTI_REGION_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGION", "eu-west", raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_HOME_REGION", "eu-west", raising=False)
    # us-east has far lower latency, but home=eu-west should lead the chain
    monkeypatch.setattr(cfg, "GATEWAY_REGIONS", "us-east:5,eu-west:90", raising=False)
    assert region_service.selected_region().name == "eu-west"
    chain = region_service.failover_chain()
    assert chain[0].name == "eu-west"
    assert {r.name for r in chain} == {"us-east", "eu-west"}


def test_current_region_always_present_in_enabled_inventory(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "MULTI_REGION_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGION", "ap-south", raising=False)
    # inventory omits the current region → it must be appended
    monkeypatch.setattr(cfg, "GATEWAY_REGIONS", "us-east:10,eu-west:80", raising=False)
    names = [r.name for r in region_service.region_inventory()]
    assert "ap-south" in names


def test_empty_regions_falls_back_to_single(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "MULTI_REGION_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGIONS", "  ,  ", raising=False)
    assert [r.name for r in region_service.region_inventory()] == ["primary"]


def test_region_status_shape(cfg):
    status = region_service.region_status()
    assert status == {
        "current": "primary",
        "home": "primary",
        "selected": "primary",
        "multi_region_enabled": False,
        "inventory": ["primary"],
    }


def test_malformed_latency_is_tolerated(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "MULTI_REGION_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGION", "a", raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_HOME_REGION", "a", raising=False)
    monkeypatch.setattr(cfg, "GATEWAY_REGIONS", "a:notanumber,b:", raising=False)
    inv = {r.name: r.latency_ms for r in region_service.region_inventory()}
    assert inv["a"] == 0.0  # bad latency → 0.0, not a crash
    assert inv["b"] == 0.0


def test_module_imports_clean():
    # guards against import-time errors in the wiring module
    importlib.reload(region_service)
