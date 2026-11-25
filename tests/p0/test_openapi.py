import pytest
from apiserver.openapi import build_openapi


def test_openapi_top_level():
    doc = build_openapi()
    assert doc["openapi"] == "3.0.3"
    assert doc["info"]["title"] == "Nemeq API"
    assert doc["info"]["version"] == "v1alpha1"


def test_openapi_paths_count_and_status():
    paths = build_openapi()["paths"]
    assert len(paths) == 17
    prefix = "/apis/nemeq.io/v1alpha1"
    for p in ("zis", "sus", "epishus", "sessions", "grants", "quotas", "models"):
        assert f"{prefix}/{p}" in paths
        assert f"{prefix}/{p}/{{name}}" in paths
    for p in ("epishus", "sessions", "quotas"):
        assert f"{prefix}/{p}/{{name}}/status" in paths


def test_openapi_components_problem():
    comps = build_openapi()["components"]
    assert "responses" in comps and "Problem" in comps["responses"]
    schema = comps["schemas"]["Problem"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"type", "title", "status", "detail", "reason"}


def test_openapi_collection_methods():
    paths = build_openapi()["paths"]
    assert set(paths["/apis/nemeq.io/v1alpha1/zis"]) == {"get", "post"}
    assert set(paths["/apis/nemeq.io/v1alpha1/zis/{name}"]) == {"get", "put", "delete"}
