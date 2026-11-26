import json
import pytest
from nqctl.client import load_config, resolve_url, DEFAULT_URL
from nqctl.commands import cmd_apply, plural_of


def test_load_config_missing_returns_empty():
    assert load_config("/nonexistent/nq/config") == {}


def test_resolve_url_priority(monkeypatch):
    monkeypatch.delenv("NEMEQ_API_URL", raising=False)
    assert resolve_url(None, {}) == DEFAULT_URL
    assert resolve_url("http://a:1/", {}) == "http://a:1"
    assert resolve_url(None, {"url": "http://b:2/"}) == "http://b:2"
    monkeypatch.setenv("NEMEQ_API_URL", "http://env:3/")
    assert resolve_url("http://explicit:4", {"url": "http://b:2"}) == "http://explicit:4"
    assert resolve_url(None, {"url": "http://b:2"}) == "http://env:3"


def test_cmd_apply_invalid_manifest_and_kind():
    class Dummy:
        pass
    code, out = cmd_apply(Dummy(), "not json")
    assert code == 400
    assert json.loads(out)["title"] == "Invalid"
    code, out = cmd_apply(Dummy(), json.dumps({"kind": "nope"}))
    assert code == 400
    assert plural_of("zi") == "zis" and plural_of("epishus") == "epishus"
    try:
        plural_of("nope")
    except ValueError:
        pass
    else:
        assert False
