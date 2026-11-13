"""Binding HTTP calls to the routes that serve them (nolli/logic/extractors/api.py)."""

from __future__ import annotations

from nolli.logic.extractors import api
from nolli.logic.extractors.facts import EntityFact, Provenance
from nolli.model.schema import EntityId


def endpoint(method: str, path: str) -> EntityFact:
    return EntityFact(EntityId(f"api:http:app{path}#{method}"), "endpoint", f"{method} {path}",
                      {"protocol": "http", "method": method, "path": path}, Provenance("test"))


ROUTES = [endpoint("GET", "/api/scene"), endpoint("GET", "/api/{name}"), endpoint("GET", "/{path:path}"),
          endpoint("POST", "/api/verdicts"), endpoint("POST", "/api/review")]


def matched(verb: str, pattern: str) -> list[str]:
    return sorted(e.attrs["path"] for e in api._match_http(ROUTES, verb, pattern)[0])


def test_a_literal_route_wins_over_parameters_and_catch_alls():
    assert matched("GET", "/api/scene") == ["/api/scene"]
    assert matched("GET", "/api/other") == ["/api/{name}"]
    assert matched("GET", "/owner/repo/pull/7") == ["/{path:path}"]


def test_routes_alike_in_specificity_all_match():
    assert matched("POST", "/api/*") == ["/api/review", "/api/verdicts"]
