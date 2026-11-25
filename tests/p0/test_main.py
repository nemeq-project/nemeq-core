import pytest
from apiserver.__main__ import main, build_parser, resolve_store, PUBLIC


def test_version_flag(capsys):
    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert "nemeq-apiserver" in out


def test_resolve_store_priority(monkeypatch, tmp_path):
    monkeypatch.delenv("NEMEQ_STORE", raising=False)
    args = build_parser().parse_args([])
    assert str(resolve_store(args)) == "/var/nemeq/apiserver"
    args = build_parser().parse_args([str(tmp_path / "explicit")])
    assert str(resolve_store(args)) == str(tmp_path / "explicit")
    monkeypatch.setenv("NEMEQ_STORE", str(tmp_path / "env"))
    args = build_parser().parse_args([])
    assert str(resolve_store(args)) == str(tmp_path / "env")
    args = build_parser().parse_args([str(tmp_path / "explicit")])
    assert "explicit" in str(resolve_store(args))


def test_build_parser_defaults():
    args = build_parser().parse_args([])
    assert args.store is None and args.host is None and args.port is None
    assert args.version is False


def test_public_constant():
    assert PUBLIC == "nq"
