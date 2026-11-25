import pytest
from apiserver.routes import parse_path, parse_request, API_PREFIX
from apiserver.errors import Problem


def test_parse_path_unknown_plural():
    try:
        parse_path(API_PREFIX + "/nope")
    except Problem as exc:
        assert exc.status == 404 and exc.reason == "NotFound"
    else:
        assert False


def test_parse_path_ok_and_status():
    assert parse_path(API_PREFIX + "/epishus") == ("epishus", None, None)
    assert parse_path(API_PREFIX + "/epishus/o") == ("epishus", "o", None)
    assert parse_path(API_PREFIX + "/epishus/o/status") == ("epishus", "o", "status")


def test_parse_request_verbs():
    assert parse_request("GET", API_PREFIX + "/zis", {})[1] == "list"
    assert parse_request("POST", API_PREFIX + "/zis", {})[1] == "create"
    assert parse_request("PUT", API_PREFIX + "/zis/a", {})[1] == "update"
    assert parse_request("PUT", API_PREFIX + "/epishus/a/status", {})[1] == "status"
    assert parse_request("DELETE", API_PREFIX + "/zis/a", {})[1] == "delete"
    assert parse_request("GET", API_PREFIX + "/zis", {"watch": ["1"]})[1] == "watch"
