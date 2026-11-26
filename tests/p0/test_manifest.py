import pytest
from nqctl.manifest import load_manifest, ManifestError


def test_load_json():
    assert load_manifest('{"kind":"Zi"}')["kind"] == "Zi"


def test_load_yaml():
    data = load_manifest("kind: Zi\nname: z\n")
    assert data["name"] == "z" and data["kind"] == "Zi"


def test_rejects_bad_input():
    with pytest.raises(ManifestError):
        load_manifest("")
    with pytest.raises(ManifestError):
        load_manifest("[]")


def test_rejects_python_object_tag():
    # yaml.FullLoader/UnsafeLoader would construct a live object here.
    with pytest.raises(ManifestError) as exc:
        load_manifest("x: !!python/object:os.system {}")
    assert "YAML" in exc.value.detail or "tag" in exc.value.detail
    with pytest.raises(ManifestError):
        load_manifest('!!python/object/apply:os.system ["id"]')
    # FullLoader would materialize a tuple / builtin here; SafeLoader must not.
    with pytest.raises(ManifestError):
        load_manifest("x: !!python/tuple [1, 2]")
    with pytest.raises(ManifestError):
        load_manifest("f: !!python/name:os.system")
