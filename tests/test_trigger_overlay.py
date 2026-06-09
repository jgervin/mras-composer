"""/trigger wires personalized overlays through the sidecar into assemble(overlay_inserts=...).

The composer image is Node-free, so the overlay is rendered by the HTTP sidecar (mocked here);
assemble itself is unchanged and mocked. Covers: personalized → overlay passed; standard → no
assemble; sidecar failure → fall back to a clip without an overlay (never drop the ad).
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from src.selector.selector import AdSelection


def _client_with(select_result, *, overlay_inserts=None, overlay_raises=False):
    """Build a TestClient with the trigger's collaborators patched. Returns (client, mocks)."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.close = AsyncMock()

    assemble = AsyncMock(return_value=Path("/output/t1.mp4"))
    synth = AsyncMock(return_value=Path("/tmp/audio.aiff"))
    build = AsyncMock(
        side_effect=RuntimeError("sidecar down") if overlay_raises else None,
        return_value=overlay_inserts,
    )

    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.select", AsyncMock(return_value=select_result)),
        patch("main.synthesize", synth),
        patch("main.assemble", assemble),
        patch("main.build_overlay_inserts_http", build),
    ]
    for p in patches:
        p.start()
    client = TestClient(main.app)
    return client, {"assemble": assemble, "build": build, "patches": patches}


def _client_with_custom(select_result, *, overlay_inserts=None, overlay_raises=False):
    """Build a TestClient patching build_custom_overlay_inserts for the custom-component path."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.close = AsyncMock()

    assemble = AsyncMock(return_value=Path("/output/t1.mp4"))
    synth = AsyncMock(return_value=Path("/tmp/audio.aiff"))
    build_custom = AsyncMock(
        side_effect=RuntimeError("sidecar down") if overlay_raises else None,
        return_value=overlay_inserts,
    )

    patches = [
        patch("main.create_pool", AsyncMock(return_value=db)),
        patch("main.select", AsyncMock(return_value=select_result)),
        patch("main.synthesize", synth),
        patch("main.assemble", assemble),
        patch("main.build_custom_overlay_inserts", build_custom),
    ]
    for p in patches:
        p.start()
    client = TestClient(main.app)
    return client, {"assemble": assemble, "build_custom": build_custom, "patches": patches}


def _stop(mocks):
    for p in mocks["patches"]:
        p.stop()


def test_personalized_trigger_passes_overlay_inserts_to_assemble():
    sel = AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                      tts_text="Welcome, Jason!", person_uuid="u1", overlay_text="Jason")
    inserts = [(Path("/tmp/ov.mov"), 500, 2000)]
    client, mocks = _client_with(sel, overlay_inserts=inserts)
    try:
        with client:
            res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                                "is_new_visitor": False})
        assert res.status_code == 200 and res.json()["status"] == "ok"
        mocks["build"].assert_awaited_once()
        _, kwargs = mocks["assemble"].call_args
        assert kwargs["overlay_inserts"] == inserts
    finally:
        _stop(mocks)


def test_standard_trigger_does_not_assemble_or_render_overlay():
    sel = AdSelection(type="standard", base_video=Path("/assets/standard.mp4"))
    client, mocks = _client_with(sel)
    try:
        with client:
            res = client.post("/trigger", json={"trigger_id": "t1", "is_new_visitor": True})
        assert res.json()["status"] == "standard"
        mocks["assemble"].assert_not_called()
        mocks["build"].assert_not_called()
    finally:
        _stop(mocks)


def test_trigger_custom_component_ad_calls_build_custom_overlay_inserts():
    sel = AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                      tts_text="Welcome, Jason!", person_uuid="u1",
                      composition_id="comp-neon",
                      overlay_props={"color": "#ff2d2d", "text": "Jason"})
    inserts = [(Path("/tmp/custom.mov"), 0, 2000)]
    client, mocks = _client_with_custom(sel, overlay_inserts=inserts)
    try:
        with client:
            res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                                "is_new_visitor": False})
        assert res.status_code == 200 and res.json()["status"] == "ok"
        mocks["build_custom"].assert_awaited_once()
        _, kwargs = mocks["assemble"].call_args
        assert kwargs["overlay_inserts"] == inserts
    finally:
        _stop(mocks)


def test_overlay_sidecar_failure_falls_back_to_no_overlay():
    sel = AdSelection(type="personalized", base_video=Path("/assets/standard.mp4"),
                      tts_text="Welcome, Jason!", person_uuid="u1", overlay_text="Jason")
    client, mocks = _client_with(sel, overlay_raises=True)
    try:
        with client:
            res = client.post("/trigger", json={"trigger_id": "t1", "uuid": "u1",
                                                "is_new_visitor": False})
        # Ad still ships, just without the overlay.
        assert res.json()["status"] == "ok"
        _, kwargs = mocks["assemble"].call_args
        assert kwargs.get("overlay_inserts") is None
    finally:
        _stop(mocks)
