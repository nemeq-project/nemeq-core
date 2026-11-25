import pytest


def test_remote_apply_reads_yaml_file_and_calls_cmd_apply(tmp_path, monkeypatch):
    import nqctl.cli as cli
    from argparse import Namespace
    manifest = tmp_path / "m.yaml"
    text = "kind: Zi\nname: z1\nspec:\n  image: i\n  digest: d\n"
    manifest.write_text(text, encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "Client", lambda url, actor: object())
    captured = {}
    def fake_cmd_apply(client, manifest_text):
        captured["client"] = client
        captured["manifest"] = manifest_text
        return (201, "ok")
    monkeypatch.setattr(cli.commands, "cmd_apply", fake_cmd_apply)
    args = Namespace(action="apply", file=str(manifest), api_url=None, actor=None)
    rc = cli._remote(args)
    assert rc == 0
    assert captured["manifest"] == text


def test_remote_apply_missing_file_returns_one(tmp_path, monkeypatch):
    import nqctl.cli as cli
    from argparse import Namespace
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "Client", lambda url, actor: object())
    called = {"n": 0}
    def fake_cmd_apply(client, text):
        called["n"] += 1
        return (201, "ok")
    monkeypatch.setattr(cli.commands, "cmd_apply", fake_cmd_apply)
    args = Namespace(action="apply", file=str(tmp_path / "nope.yaml"), api_url=None, actor=None)
    rc = cli._remote(args)
    assert rc == 1
    assert called["n"] == 0


def test_remote_apply_yaml_error_returns_one(tmp_path, monkeypatch):
    import nqctl.cli as cli
    from argparse import Namespace
    manifest = tmp_path / "bad.yaml"
    manifest.write_text("x: !!python/object:os.system {}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "Client", lambda url, actor: object())
    monkeypatch.setattr(cli.commands, "cmd_apply", lambda client, text: (400, "bad"))
    args = Namespace(action="apply", file=str(manifest), api_url=None, actor=None)
    assert cli._remote(args) == 1


def test_cmd_apply_yaml_calls_create():
    """Command layer: YAML manifest -> client.create(plural, parsed_object)."""
    import json

    from nqctl.commands import cmd_apply

    calls = []

    class Client:
        def create(self, plural, body):
            calls.append((plural, body))
            return {"metadata": {"name": body["name"]}}

    yaml_text = "kind: Zi\nname: z1\nspec:\n  image: i\n  digest: d\n"
    code, out = cmd_apply(Client(), yaml_text)
    assert code == 201
    assert calls == [
        (
            "zis",
            {"kind": "Zi", "name": "z1", "spec": {"image": "i", "digest": "d"}},
        )
    ]
    assert json.loads(out)["metadata"]["name"] == "z1"


def test_cmd_apply_json_calls_create():
    from nqctl.commands import cmd_apply

    calls = []

    class Client:
        def create(self, plural, body):
            calls.append((plural, body))
            return body

    code, _ = cmd_apply(Client(), '{"kind": "Su", "name": "s1", "spec": {}}')
    assert code == 201
    assert calls[0][0] == "sus"
