from __future__ import annotations

import json
from typing import Any

try:
    import yaml
    _HAS_YAML: bool = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


class ManifestError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail: str = detail


def _safe_yaml_load(text: str) -> Any:
    if not _HAS_YAML:
        raise ManifestError("YAML support requires pyyaml")
    try:
        return yaml.load(text, Loader=yaml.SafeLoader)  # type: ignore[arg-type]
    except yaml.YAMLError as exc:  # type: ignore[attr-defined]
        raise ManifestError(f"invalid YAML: {exc}") from exc


def _reject_custom_tags(node: Any) -> None:
    if isinstance(node, dict):
        for key in node:
            if isinstance(key, str) and key.startswith("!!python"):
                raise ManifestError(f"custom YAML tag in key: {key!r}")
        for value in node.values():
            _reject_custom_tags(value)
    elif isinstance(node, list):
        for item in node:
            _reject_custom_tags(item)
    elif isinstance(node, str):
        if node.startswith("!!python"):
            raise ManifestError(f"custom YAML tag in value: {node!r}")


def load_manifest(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise ManifestError("manifest is empty")
    try:
        result: Any = json.loads(stripped)
    except ValueError:
        if not _HAS_YAML:
            raise ManifestError("YAML support requires pyyaml")
        try:
            result = _safe_yaml_load(stripped)
        except ManifestError:
            raise
    if not isinstance(result, dict):
        raise ManifestError("manifest must be a mapping")
    _reject_custom_tags(result)
    return result  # type: ignore[return-value]


def load_manifest_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="strict") as f:
            text = f.read()
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest is not UTF-8") from exc
    except OSError as exc:
        raise ManifestError(str(exc)) from exc
    return load_manifest(text)


def dumps_manifest(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)
