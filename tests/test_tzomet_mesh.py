from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MESH_PATH = Path(__file__).resolve().parents[1] / "tools/tzomet_mesh.py"


def load_mesh():
    spec = importlib.util.spec_from_file_location("tzomet_mesh_under_test", MESH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relay_load_requires_explicit_matching_pin(tmp_path: Path) -> None:
    mesh = load_mesh()
    relay = tmp_path / "tzomet_twin_relay.py"
    relay.write_text("WIRE_FORMAT = 'canonical'\n", encoding="utf-8")
    pin = mesh.sha256_file(relay)
    loaded, loaded_path = mesh.load_twin_relay(relay, pin)
    assert loaded.WIRE_FORMAT == "canonical"
    assert loaded_path == relay.resolve()


def test_relay_pin_mismatch_fails_before_import(tmp_path: Path) -> None:
    mesh = load_mesh()
    relay = tmp_path / "tzomet_twin_relay.py"
    relay.write_text("raise AssertionError('must not import')\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        mesh.load_twin_relay(relay, "0" * 64)
    assert raised.value.code == 2


def test_default_relay_path_is_the_protected_checkout() -> None:
    mesh = load_mesh()
    assert mesh.DEFAULT_RELAY_MODULE == (
        Path.home() / "tzomet_twin_relay_repo/tools/tzomet_twin_relay.py"
    )
