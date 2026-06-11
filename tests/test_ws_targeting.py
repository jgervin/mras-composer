from unittest.mock import AsyncMock

from main import WSManager


def _ws():
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


async def test_send_to_targets_only_the_matching_screen():
    m = WSManager()
    ws1, ws2 = _ws(), _ws()
    await m.connect(ws1, "display-1")
    await m.connect(ws2, "display-2")

    await m.send_to("display-2", {"type": "play"})

    ws2.send_json.assert_awaited_once()
    ws1.send_json.assert_not_awaited()


async def test_screen_ids_lists_tagged_clients_sorted():
    m = WSManager()
    await m.connect(_ws(), "display-2")
    await m.connect(_ws(), "display-1")
    await m.connect(_ws())  # legacy untagged client
    assert m.screen_ids() == ["display-1", "display-2"]


async def test_broadcast_still_reaches_everyone_including_untagged():
    m = WSManager()
    tagged, untagged = _ws(), _ws()
    await m.connect(tagged, "display-1")
    await m.connect(untagged)

    await m.broadcast({"type": "play"})

    tagged.send_json.assert_awaited_once()
    untagged.send_json.assert_awaited_once()


async def test_send_to_drops_dead_clients_quietly():
    m = WSManager()
    dead = _ws()
    dead.send_json = AsyncMock(side_effect=RuntimeError("gone"))
    await m.connect(dead, "display-1")

    await m.send_to("display-1", {"type": "play"})  # must not raise
    assert m.screen_ids() == []  # dead client evicted
