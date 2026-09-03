"""Threat model L8: a stray OTEL_EXPORTER_OTLP_ENDPOINT must never silently
enable trace export — no exporter is configured anywhere in this codebase, and
spans are not vetted for identity leakage. The startup guard logs CRITICAL and
configures nothing."""

import logging

import pytest

from src.services.startup import _check_otlp_exporter_guard


@pytest.fixture(autouse=True)
def _clean_otlp_env(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)


def test_no_warning_when_otlp_env_unset(caplog):
    with caplog.at_level(logging.CRITICAL, logger="src.services.startup"):
        _check_otlp_exporter_guard()
    assert not any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_critical_logged_when_otlp_endpoint_set(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://attacker.example:4318")
    with caplog.at_level(logging.CRITICAL, logger="src.services.startup"):
        _check_otlp_exporter_guard()

    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical_records) == 1
    assert "otlp_exporter_env_present_but_export_disabled" in critical_records[0].message


def test_critical_logged_when_otlp_traces_endpoint_set(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://attacker.example:4318")
    with caplog.at_level(logging.CRITICAL, logger="src.services.startup"):
        _check_otlp_exporter_guard()

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
