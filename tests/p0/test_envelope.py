import pytest
from apiserver.envelope import to_envelope, from_envelope, to_list, API_VERSION


def test_to_envelope_shape():
    stored = {"name": "o", "spec": {"ziRef": "z"}, "status": {},
              "uid": "u1", "generation": 2, "resourceVersion": "5-u1"}
    env = to_envelope("Epishu", stored)
    assert env["apiVersion"] == API_VERSION
    assert env["kind"] == "Epishu"
    assert env["metadata"] == {"name": "o", "uid": "u1",
                               "resourceVersion": "5-u1", "generation": 2}
    assert env["spec"] == {"ziRef": "z"}


def test_to_envelope_missing_fields():
    env = to_envelope("Zi", {})
    assert env["status"] == {}
    assert env["metadata"]["name"] == ""
    assert env["metadata"]["generation"] == 0
    assert env["status"] == {}


def test_from_envelope_flat_and_meta():
    flat = from_envelope("Epishu", {"name": "o", "spec": {"ziRef": "z"}})
    assert flat == {"name": "o", "spec": {"ziRef": "z"}, "status": {}}
    meta = from_envelope("Epishu", {"apiVersion": "nemeq.io/v1alpha1",
                                     "kind": "Epishu", "metadata": {"name": "o"},
                                     "spec": {"ziRef": "z"}})
    assert meta["name"] == "o"


def test_from_envelope_errors():
    with pytest.raises(ValueError):
        from_envelope("Epishu", {})
    with pytest.raises(ValueError):
        from_envelope("Epishu", {"kind": "Zi", "name": "x", "spec": {}})


def test_to_list_shape():
    page = to_list("Zi", [{"name": "z", "spec": {}}], next_token="7")
    assert page["kind"] == "ZiList"
    assert page["metadata"]["continue"] == "7"
    assert len(page["items"]) == 1
