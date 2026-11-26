import pytest
from massaru.store import Store
from apiserver.errors import Problem
from apiserver.handlers import Api


def make_api(tmp_path, grants):
    store = Store(tmp_path / "store")
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store.put("grants/admin", admin, expected_rev=0)
    for name, subject, role, scope, verbs in grants:
        store.put(f"grants/{name}", {"name": name, "spec": {"subject": subject, "role": role, "scope": scope, "verbs": verbs}}, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    return api, store


def test_scoped_pagination_skips_hidden_between_pages(tmp_path):
    api, store = make_api(tmp_path, [
        ("v1", "v", "viewer", "epishu/vis-a", ["list"]),
        ("v2", "v", "viewer", "epishu/vis-b", ["list"]),
    ])
    for n in ("h1", "h2", "vis-a", "h3", "vis-b", "h4"):
        api.create("epishus", {"name": n, "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    page1 = api.list("epishus", limit=1, actor="v")
    assert [i["metadata"]["name"] for i in page1["items"]] == ["vis-a"]
    assert page1["metadata"]["continue"] == "1"
    page2 = api.list("epishus", limit=1, continue_token="1", actor="v")
    assert [i["metadata"]["name"] for i in page2["items"]] == ["vis-b"]
    assert page2["metadata"]["continue"] == ""
    all_names = [i["metadata"]["name"] for i in page1["items"] + page2["items"]]
    assert "h" not in "".join(all_names)
    store.close()


def test_continue_offset_does_not_leak_global_index(tmp_path):
    api, store = make_api(tmp_path, [("v", "v", "viewer", "epishu/only", ["list"])])
    for n in ("h1", "h2", "h3", "h4", "h5"):
        api.create("epishus", {"name": n, "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    api.create("epishus", {"name": "only", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    page = api.list("epishus", limit=10, actor="v")
    assert [i["metadata"]["name"] for i in page["items"]] == ["only"]
    assert page["metadata"]["continue"] == ""
    page2 = api.list("epishus", limit=10, continue_token="1", actor="v")
    assert page2["items"] == []
    store.close()


def test_foreign_delete_event_filtered_from_watch(tmp_path):
    api, store = make_api(tmp_path, [("w", "w", "viewer", "epishu/mine", ["watch"])])
    api.create("epishus", {"name": "mine", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    api.create("epishus", {"name": "theirs", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    store.delete("epishus/theirs", actor="root")
    page = api.watch_page("epishus", from_rev=0, limit=100, actor="w")
    deleted = [i for i in page["items"] if i["type"] == "DELETED"]
    assert deleted == []
    assert page["metadata"]["nextRev"] > 0
    store.delete("epishus/mine", actor="root")
    page2 = api.watch_page("epishus", from_rev=page["metadata"]["nextRev"], limit=100, actor="w")
    deleted2 = [i for i in page2["items"] if i["type"] == "DELETED"]
    assert len(deleted2) == 1
    assert deleted2[0]["object"] is None
    store.close()


def test_session_delete_scope_uses_internal_epishu_ref(tmp_path):
    api, store = make_api(tmp_path, [("s", "s", "viewer", "epishu/one", ["watch"])])
    api.create("sessions", {"name": "s1", "spec": {"epishuRef": "one", "principal": "u1"}}, actor="root")
    api.create("sessions", {"name": "s2", "spec": {"epishuRef": "two", "principal": "u2"}}, actor="root")
    store.delete("sessions/s1", actor="root")
    store.delete("sessions/s2", actor="root")
    page = api.watch_page("sessions", from_rev=0, limit=100, actor="s")
    deleted = [i for i in page["items"] if i["type"] == "DELETED"]
    assert len(deleted) == 1
    assert deleted[0]["object"] is None
    store.close()


def test_get_only_actor_cannot_list(tmp_path):
    api, store = make_api(tmp_path, [("g", "g", "viewer", "*", ["get"])])
    try:
        api.list("epishus", actor="g")
    except Problem as exc:
        assert exc.status == 403
    else:
        assert False
    store.close()


def test_get_only_actor_cannot_watch(tmp_path):
    api, store = make_api(tmp_path, [("g", "g", "viewer", "*", ["get"])])
    try:
        api.watch_page("epishus", from_rev=0, actor="g")
    except Problem as exc:
        assert exc.status == 403
    else:
        assert False
    store.close()


def test_denied_list_writes_audit(tmp_path):
    api, store = make_api(tmp_path, [("g", "g", "viewer", "*", ["get"])])
    try:
        api.list("epishus", actor="g")
    except Problem:
        pass
    audits, _ = store.list("audits/")
    assert any(
        a["auditDeny"]["verb"] == "list" and a["auditDeny"]["status"] == 403
        for a in audits
    )
    store.close()


def test_hidden_objects_do_not_force_a_continue_token(tmp_path):
    """Cursor counts within the filtered list, so it never leaks how many
    hidden objects exist outside the caller's scope."""
    api, store = make_api(
        tmp_path,
        [
            ("a", "v", "viewer", "epishu/vis-a", ["list"]),
            ("b", "v", "viewer", "epishu/vis-b", ["list"]),
        ],
    )
    for n in ("h1", "h2", "vis-a", "h3", "vis-b", "h4", "h5"):
        api.create("epishus", {"name": n, "spec": {"ziRef": "z", "suRef": "s"}},
                   actor="root")
    page1 = api.list("epishus", limit=1, actor="v")
    assert [i["metadata"]["name"] for i in page1["items"]] == ["vis-a"]
    assert page1["metadata"]["continue"] == "1"
    page2 = api.list("epishus", limit=1, continue_token="1", actor="v")
    assert [i["metadata"]["name"] for i in page2["items"]] == ["vis-b"]
    assert page2["metadata"]["continue"] == ""
    store.close()
