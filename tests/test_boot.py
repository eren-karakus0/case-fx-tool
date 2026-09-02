"""The application starts, and it starts against whatever it was pointed at.

The rest of the suite replaces the runtime through a dependency override, which
means nothing else would notice if the real startup path were broken.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import MAX_AMOUNT
from app.main import app
from app.upstream import Upstream

CLOSED_PORT = "http://127.0.0.1:1"


def test_the_application_builds_its_client_and_cache_at_startup(monkeypatch):
    # No socket is opened here: a client is constructed, not connected.
    monkeypatch.setenv("FX_UPSTREAM_BASE", CLOSED_PORT)

    with TestClient(app):
        assert isinstance(app.state.upstream, Upstream)
        assert app.state.settings.upstream_root == f"{CLOSED_PORT}/v1"


def test_startup_refuses_a_base_url_that_could_never_work(monkeypatch):
    monkeypatch.setenv("FX_UPSTREAM_BASE", "not-a-url")

    with pytest.raises(ValueError):
        with TestClient(app):
            pass


def test_the_published_schema_names_the_parameters_the_brief_documents():
    # The schema is how a model learns to call this. If a parameter were renamed
    # to something Python-friendly, every documented call would silently take a
    # default instead.
    schema = app.openapi()["paths"]["/tools/convert"]["get"]
    names = {parameter["name"] for parameter in schema["parameters"]}

    assert {"amount", "from", "to", "date"} <= names


def test_the_published_schema_states_the_constraints_on_the_amount():
    schema = app.openapi()["paths"]["/tools/convert"]["get"]
    amount = next(p for p in schema["parameters"] if p["name"] == "amount")
    # A Decimal is published as "a number, or the text of one", so the numeric
    # constraints sit on the number branch rather than at the top level.
    numeric = next(
        branch for branch in amount["schema"]["anyOf"] if branch.get("type") == "number"
    )

    assert amount["required"] is True
    assert numeric["exclusiveMinimum"] == 0
    assert numeric["maximum"] == float(MAX_AMOUNT)


def test_only_the_documented_endpoint_is_served():
    # Anything extra is scope the brief explicitly does not want, and each one
    # is another surface to keep correct.
    served = {
        route.path
        for route in app.routes
        if getattr(route, "methods", None)
        and not route.path.startswith(("/docs", "/redoc", "/openapi"))
    }

    assert served == {"/tools/convert"}
