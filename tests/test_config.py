"""The upstream host is configuration, not a constant baked into the code."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.config import DEFAULT_UPSTREAM_BASE, load_settings

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def test_defaults_to_the_public_upstream_when_the_variable_is_unset():
    assert load_settings({}).upstream_base == DEFAULT_UPSTREAM_BASE


def test_an_empty_variable_is_treated_as_unset():
    # A container that exports FX_UPSTREAM_BASE="" should behave like one that
    # never set it, rather than building requests against "/v1/latest".
    assert load_settings({"FX_UPSTREAM_BASE": "   "}).upstream_base == DEFAULT_UPSTREAM_BASE


def test_the_environment_wins():
    settings = load_settings({"FX_UPSTREAM_BASE": "http://127.0.0.1:9"})
    assert settings.upstream_base == "http://127.0.0.1:9"


def test_the_versioned_root_is_built_from_the_base():
    settings = load_settings({"FX_UPSTREAM_BASE": "http://fake.test/"})
    # The trailing slash must not survive into request URLs.
    assert settings.upstream_root == "http://fake.test/v1"


@pytest.mark.parametrize("value", ["api.frankfurter.dev", "ftp://x/y", "https://", "/v1"])
def test_a_base_that_is_not_an_absolute_http_url_fails_at_startup(value):
    # Better a refused boot than a request-time crash that reads as an outage.
    with pytest.raises(ValueError):
        load_settings({"FX_UPSTREAM_BASE": value})


def test_the_real_host_appears_only_as_the_default_in_config():
    """Nothing outside config.py may mention the real upstream.

    This is the requirement the reviewer verifies by pointing the service at a
    fake host, so it is worth asserting rather than trusting.
    """
    offenders = [
        path.name
        for path in APP_DIR.rglob("*.py")
        if path.name != "config.py" and "frankfurter" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_config_mentions_the_host_exactly_once():
    source = Path(config.__file__).read_text(encoding="utf-8")
    # Twice would mean a second copy has crept in somewhere below the default.
    assert source.count("api.frankfurter.dev") == 1
