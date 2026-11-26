import pytest
from apiserver._testkit import API, call, server  # noqa: F401
from apiserver.watchpage import page_events, next_rev_of


def test_next_rev_advances_on_filtered_events():
    grants = [{"spec": {"subject": "v", "role": "viewer", "scope": "epishu/one", "verbs": []}}]
    events = [{"rev": 1, "seq": 0, "type": "ADDED", "key": "epishus/one",
               "value": {"name": "one", "spec": {}, "resourceVersion": "1-a"}},
              {"rev": 2, "seq": 0, "type": "ADDED", "key": "epishus/two",
               "value": {"name": "two", "spec": {}, "resourceVersion": "2-b"}}]
    page = page_events("Epishu", "epishus", events, from_rev=0, limit=100,
                       grants=grants, actor="v")
    assert [i["rev"] for i in page["items"]] == [1]
    assert page["metadata"]["nextRev"] == 2


def test_cross_plural_advances_watermark():
    grants = [{"spec": {"subject": "r", "role": "admin", "scope": "*", "verbs": []}}]
    events = [{"rev": 1, "seq": 0, "type": "ADDED", "key": "grants/admin",
               "value": {"name": "admin", "spec": {}}},
              {"rev": 2, "seq": 0, "type": "ADDED", "key": "epishus/one",
               "value": {"name": "one", "spec": {}}}]
    page = page_events("Epishu", "epishus", events, from_rev=0, limit=100,
                       grants=grants, actor="r")
    assert [i["rev"] for i in page["items"]] == [2]
    assert page["metadata"]["nextRev"] == 2


def test_same_rev_group_not_split():
    grants = [{"spec": {"subject": "r", "role": "admin", "scope": "*", "verbs": []}}]
    events = [{"rev": 5, "seq": 0, "type": "ADDED", "key": "epishus/a",
               "value": {"name": "a", "spec": {}}},
              {"rev": 5, "seq": 1, "type": "ADDED", "key": "epishus/b",
               "value": {"name": "b", "spec": {}}},
              {"rev": 6, "seq": 0, "type": "ADDED", "key": "epishus/c",
               "value": {"name": "c", "spec": {}}}]
    page = page_events("Epishu", "epishus", events, from_rev=4, limit=1,
                       grants=grants, actor="r")
    assert len(page["items"]) == 2 and [i["rev"] for i in page["items"]] == [5, 5]
    assert page["metadata"]["nextRev"] == 5
    rest = page_events("Epishu", "epishus", events, from_rev=5, limit=10,
                       grants=grants, actor="r")
    assert [i["rev"] for i in rest["items"]] == [6]
    assert rest["metadata"]["nextRev"] == 6


def test_session_deleted_uses_internal_epishu_ref():
    # Session scope comes from spec.epishuRef, which only exists in the
    # internal previous value of a DELETED event.
    grants = [{"spec": {"subject": "w", "role": "viewer",
                        "scope": "epishu/one", "verbs": []}}]
    events = [{"rev": 5, "seq": 0, "type": "DELETED", "key": "sessions/s1",
               "value": {"name": "s1", "spec": {"epishuRef": "one"}}}]
    page = page_events("Session", "sessions", events, from_rev=4, limit=10,
                       grants=grants, actor="w")
    assert [i["type"] for i in page["items"]] == ["DELETED"]
    assert page["items"][0]["object"] is None
    assert page["metadata"]["nextRev"] == 5


def test_rev_group_overflow_keeps_group_atomic():
    grants = [{"spec": {"subject": "r", "role": "admin", "scope": "*", "verbs": []}}]
    events = [
        {"rev": 5, "seq": 0, "type": "ADDED", "key": "epishus/a",
         "value": {"name": "a", "spec": {}}},
        {"rev": 6, "seq": 0, "type": "ADDED", "key": "epishus/b",
         "value": {"name": "b", "spec": {}}},
        {"rev": 6, "seq": 1, "type": "ADDED", "key": "epishus/c",
         "value": {"name": "c", "spec": {}}},
    ]
    page = page_events("Epishu", "epishus", events, from_rev=4, limit=2,
                       grants=grants, actor="r")
    assert [i["rev"] for i in page["items"]] == [5]
    assert page["metadata"]["nextRev"] == 5
    rest = page_events("Epishu", "epishus", events, from_rev=5, limit=2,
                       grants=grants, actor="r")
    assert [i["rev"] for i in rest["items"]] == [6, 6]
    assert rest["metadata"]["nextRev"] == 6


def test_deleted_uses_previous_scope():
    grants = [{"spec": {"subject": "v", "role": "viewer", "scope": "epishu/one", "verbs": []}}]
    events = [{"rev": 5, "seq": 0, "type": "DELETED", "key": "epishus/two",
               "value": {"name": "two", "spec": {}}}]
    page = page_events("Epishu", "epishus", events, from_rev=4, limit=10,
                       grants=grants, actor="v")
    assert page["items"] == [] and page["metadata"]["nextRev"] == 5


def test_deleted_allowed_scope_visible(server):
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "one", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    call(s, "DELETE", f"{API}/epishus/one", actor="root")
    status, body = call(s, "GET", f"{API}/epishus?watch=1&from_rev=0", actor="root")
    assert status == 200
    types = [i["type"] for i in body["items"]]
    assert "DELETED" in types
    deleted = [i for i in body["items"] if i["type"] == "DELETED"][0]
    assert deleted["object"] is None


def test_http_watch_empty_backlog_next_rev(server):
    s = server[0]
    status, body = call(s, "GET", f"{API}/epishus?watch=1&from_rev=1", actor="root")
    assert status == 200 and body["items"] == []
    assert body["metadata"]["nextRev"] == 1


def test_http_watch_cross_plural_watermark(server):
    s = server[0]
    call(s, "POST", f"{API}/zis",
         body={"name": "z1", "spec": {"image": "i", "digest": "d"}}, actor="root")
    status, body = call(s, "GET", f"{API}/epishus?watch=1&from_rev=1", actor="root")
    assert status == 200 and body["items"] == []
    assert body["metadata"]["nextRev"] == 2
