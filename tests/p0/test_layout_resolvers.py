"""Тесты резолверов путей (NEMEQ_HOME_LAYOUT v0.6 §5, мутанты M-5.x).

Каждый тест соответствует мутанту из спецификации: если убрать проверку в коде,
тест обязан упасть.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from massaru.layout import (
    PathError,
    lock_path,
    resolve_all,
    resolve_home,
    resolve_instance,
    resolve_store,
    resolve_var,
)


class TestHome:
    def test_explicit_wins(self, tmp_path):
        assert resolve_home(str(tmp_path), env={"HOME": "/home/x", "NEMEQ_HOME": "/env"}) == tmp_path

    def test_env_over_home(self, tmp_path):
        # M-5.1: убрать приоритет env над $HOME — тест падает
        got = resolve_home(None, env={"HOME": "/home/x", "NEMEQ_HOME": str(tmp_path)})
        assert got == tmp_path

    def test_default_from_home(self):
        got = resolve_home(None, env={"HOME": "/home/x"})
        assert got == Path("/home/x/.nemeq")

    def test_relative_rejected(self):
        # M-5.2: относительный путь приводится к абсолютному от CWD — тест падает
        with pytest.raises(PathError):
            resolve_home("./relative", env={"HOME": "/home/x"})

    def test_missing_home_rejected(self):
        # M-5.3: фоллбэк на CWD или / — тест падает
        with pytest.raises(PathError):
            resolve_home(None, env={})

    def test_empty_home_rejected(self):
        with pytest.raises(PathError):
            resolve_home(None, env={"HOME": ""})


class TestInstance:
    def test_default(self):
        assert resolve_instance(None, env={}) == "default"

    def test_env_and_explicit(self):
        assert resolve_instance(None, env={"NEMEQ_INSTANCE": "alpha"}) == "alpha"
        assert resolve_instance("beta", env={"NEMEQ_INSTANCE": "alpha"}) == "beta"

    @pytest.mark.parametrize("bad", ["../x", "/x", "a/b", "A", "", "x" * 40, "with space"])
    def test_invalid_names_rejected(self, bad):
        # M-5.4: убрать валидацию имени — тест падает на ../x
        with pytest.raises(PathError):
            resolve_instance(bad, env={})

    def test_valid_names(self):
        for good in ("default", "alpha", "a", "a1", "a-b_c", "x" * 32):
            assert resolve_instance(good, env={}) == good


class TestVarStore:
    def test_defaults(self, tmp_path):
        r = resolve_all(home=str(tmp_path), env={})
        assert r.var == tmp_path / "instances" / "default" / "var"
        assert r.store == r.var / "store"

    def test_instance_scoped(self, tmp_path):
        r = resolve_all(home=str(tmp_path), instance="alpha", env={})
        assert r.var == tmp_path / "instances" / "alpha" / "var"

    def test_state_root_override(self, tmp_path):
        other = tmp_path / "other"
        r = resolve_all(home=str(tmp_path), state_root=str(other), env={})
        assert r.var == other

    def test_explicit_state_root_allowed_outside_home(self, tmp_path):
        """Явный --state-root законен вне home: это осознанное решение оператора."""
        got = resolve_var(tmp_path, "default", explicit="/somewhere/else")
        assert got == Path("/somewhere/else")

    def test_env_state_root_outside_home_rejected(self, tmp_path):
        # M-5.5: env-переопределение выводит стор наружу без флага — тест падает
        with pytest.raises(PathError):
            resolve_var(tmp_path, "default", env={"NEMEQ_VAR_ROOT": "/somewhere/else"})

    def test_env_state_root_outside_home_allowed(self, tmp_path):
        got = resolve_var(tmp_path, "default",
                          env={"NEMEQ_VAR_ROOT": "/somewhere/else"},
                          allow_outside_home=True)
        assert got == Path("/somewhere/else")

    def test_store_override(self, tmp_path):
        var = tmp_path / "var"
        r = resolve_all(home=str(tmp_path), store=str(var / "s2"), env={})
        assert r.store == var / "s2"

    def test_env_sources_reported(self, tmp_path):
        # M-5.6: не печатать источник пути — тест падает
        r = resolve_all(home=str(tmp_path), env={"NEMEQ_VAR_ROOT": str(tmp_path / "v")})
        assert r.sources["var"] == "env(NEMEQ_VAR_ROOT)"
        assert r.sources["home"] == "--home"


class TestLockPath:
    def test_inside_var_locks(self, tmp_path):
        # M-6.4-a/h: лок внутри store/ или вне var/locks — тест падает
        var = tmp_path / "var"
        store = var / "store"
        lp = lock_path(var, store)
        assert lp.parent == var / "locks"
        assert lp.name.endswith(".lock")

    def test_full_sha256_not_truncated(self, tmp_path):
        # M-6.4-g: усечь хеш до 16 символов — тест падает
        var = tmp_path / "var"
        lp = lock_path(var, var / "store")
        stem = lp.stem
        assert len(stem) == 64, f"expected full sha256 (64 hex), got {len(stem)}"

    def test_identity_by_realpath_not_by_name(self, tmp_path):
        # M-6.4-d: считать путь лока от строки, а не от realpath — тест падает
        var = tmp_path / "var"
        real = tmp_path / "real_store"
        real.mkdir()
        link = tmp_path / "link_store"
        link.symlink_to(real)
        assert lock_path(var, real) == lock_path(var, link)

    def test_different_stores_differ(self, tmp_path):
        var = tmp_path / "var"
        a = lock_path(var, var / "s1")
        b = lock_path(var, var / "s2")
        assert a != b
