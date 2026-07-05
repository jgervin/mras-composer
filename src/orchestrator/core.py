import time
from dataclasses import dataclass
from typing import Callable

from src.orchestrator.commands import EvictRender, Idle, Play, RenderAhead
from src.orchestrator.model import Round, even_split, next_round, pair_slot


@dataclass
class _Program:
    uuid: str
    first_seen: float
    round: Round = Round.OPENER
    # Camera screen_id from the trigger that (re)identified this subject. Threaded
    # onto Play/RenderAhead so the render lane can camera-scope its God View events.
    screen_id: str | None = None
    # Last time this subject was seen (identify or presence heartbeat). Drives the
    # abandon-TTL sweep in tick() (issue #36).
    last_present: float = 0.0


@dataclass
class _Screen:
    owner: str | None = None
    round: Round | None = None
    playing: bool = False


class Orchestrator:
    def __init__(self, displays: list[str], clock=time.monotonic,
                 presence_ttl_s: float = 5.0,
                 # Consolidation tripwire: the SECOND time/dwell-policy callable added
                 # here → consolidate into a single policy object (issue #36 round-2 record).
                 abandon_ttl_s: Callable[[_Program], float] = lambda p: 900.0) -> None:
        self._displays = list(displays)
        self._clock = clock
        self._ttl = presence_ttl_s
        self._abandon_ttl_s = abandon_ttl_s
        self._programs: dict[str, _Program] = {}
        self._present: dict[str, float] = {}
        self._screens: dict[str, _Screen] = {d: _Screen() for d in self._displays}

    # ---- event handlers (each returns a list of Command) ----

    def on_identify(self, uuid: str, screen_id: str | None = None) -> list:
        now = self._clock()
        prog = self._programs.get(uuid)
        if prog is None or prog.round == Round.DONE:
            self._programs[uuid] = _Program(uuid, first_seen=now, last_present=now)
        # Always stamp the latest triggering camera so the render lane resolves
        # scope from wherever this subject was most recently seen.
        self._programs[uuid].screen_id = screen_id
        self._programs[uuid].last_present = now
        self._present[uuid] = now
        return self._reassign()

    def on_clip_ended(self, display: str) -> list:
        # A kiosk may connect with a screen_id outside the configured displays;
        # ignore clip_ended for unknown displays rather than raising KeyError.
        sc = self._screens.get(display)
        if sc is None:
            return []
        sc.playing = False
        owner = sc.owner
        if owner is not None and owner in self._programs \
                and self._programs[owner].round == sc.round:
            # first display of this owner to finish the current round → advance
            self._programs[owner].round = next_round(self._programs[owner].round)
            if self._programs[owner].round == Round.DONE:
                # program boundary: evict the owner's cached renders so a later
                # fresh program can't replay a stale trigger_id (issue #27)
                return [EvictRender(owner)] + self._reassign()
        return self._reassign()

    def on_presence(self, uuids: list[str]) -> list:
        now = self._clock()
        for uuid in uuids:
            self._present[uuid] = now
            if uuid in self._programs:
                self._programs[uuid].last_present = now
        return self.tick()

    def tick(self) -> list:
        now = self._clock()
        expired = [u for u, seen in self._present.items() if now - seen > self._ttl]
        for u in expired:
            del self._present[u]
        # Abandon-TTL sweep (issue #36): forget programs whose subject has been gone
        # longer than the policy window so a returning visitor restarts with a fresh
        # opener. Also GCs leaked DONE entries — those were already evicted at their
        # program boundary (issue #27), so no duplicate EvictRender for them.
        cmds: list = []
        for u, p in list(self._programs.items()):
            if u in self._present:
                continue
            if now - p.last_present > self._abandon_ttl_s(p):
                del self._programs[u]
                if p.round != Round.DONE:
                    cmds.append(EvictRender(u))
        return cmds + self._reassign()

    # ---- internals ----

    def _active_newest_first(self) -> list[str]:
        active = [u for u, p in self._programs.items()
                  if p.round != Round.DONE and u in self._present]
        return sorted(active, key=lambda u: self._programs[u].first_seen, reverse=True)

    def _reassign(self) -> list:
        split = even_split(self._active_newest_first(), self._displays)
        owned: dict[str, list[str]] = {}
        for disp, owner in split.items():
            owned.setdefault(owner, []).append(disp)
        cmds: list = []
        render_ahead_owners: list[str] = []  # one render-ahead per owner, not per display
        for disp in self._displays:
            sc = self._screens[disp]
            if sc.playing:
                continue  # never interrupt a personalized clip mid-play
            new_owner = split.get(disp)
            if new_owner is None:
                if sc.owner is not None:
                    sc.owner, sc.round = None, None
                    cmds.append(Idle(disp))
                continue
            rnd = self._programs[new_owner].round
            # Opener plays one shared render on every owned display (slot 0);
            # only round 2 splits into the A/B pair.
            slot = pair_slot(disp, sorted(owned[new_owner])) if rnd == Round.ROUND2 else 0
            sc.owner, sc.round, sc.playing = new_owner, rnd, True
            cmds.append(Play(disp, new_owner, rnd, slot,
                             self._programs[new_owner].screen_id))
            if rnd == Round.OPENER and new_owner not in render_ahead_owners:
                render_ahead_owners.append(new_owner)
        cmds.extend(RenderAhead(o, Round.ROUND2, self._programs[o].screen_id)
                    for o in render_ahead_owners)
        return cmds
