from src.orchestrator.commands import EvictRender, Play, Idle, RenderAhead
from src.orchestrator.model import Round
from src.orchestrator.core import Orchestrator


class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _orch(displays=("display-1", "display-2", "display-3", "display-4")):
    return Orchestrator(list(displays), clock=_Clock(), presence_ttl_s=5.0)


def test_commands_are_value_equal_and_hashable():
    assert Play("display-1", "jason", Round.OPENER, 0) == Play("display-1", "jason", Round.OPENER, 0)
    assert Idle("display-2") == Idle("display-2")
    assert RenderAhead("jason", Round.ROUND2) == RenderAhead("jason", Round.ROUND2)
    # frozen → usable in sets
    assert len({Idle("display-1"), Idle("display-1")}) == 1


def test_on_identify_starts_opener_on_all_idle_displays_and_renders_ahead():
    o = _orch()
    cmds = o.on_identify("jason")
    # opener (round OPENER, slot 0) on all four idle displays
    plays = [c for c in cmds if isinstance(c, Play)]
    assert plays == [
        Play("display-1", "jason", Round.OPENER, 0),
        Play("display-2", "jason", Round.OPENER, 0),
        Play("display-3", "jason", Round.OPENER, 0),
        Play("display-4", "jason", Round.OPENER, 0),
    ]
    # exactly one render-ahead for the round-2 pair
    assert RenderAhead("jason", Round.ROUND2) in cmds
    assert sum(isinstance(c, RenderAhead) for c in cmds) == 1


def test_clip_ended_advances_to_round2_paired_AABB():
    o = _orch()
    o.on_identify("jason")  # opener on 1..4 (all now playing)
    # all four openers end (first one advances the program to round 2)
    cmds = []
    for d in ["display-1", "display-2", "display-3", "display-4"]:
        cmds = o.on_clip_ended(d)
    # after the last clip_ended, every display projects round 2, paired A,A,B,B
    plays = {c.display: c for c in cmds if isinstance(c, Play)}
    # the last-ended display (display-4) is reassigned in this call
    assert plays["display-4"] == Play("display-4", "jason", Round.ROUND2, 1)


def test_first_opener_end_advances_program_once():
    o = _orch(displays=("display-1", "display-2"))
    o.on_identify("jason")            # opener on 1,2
    cmds1 = o.on_clip_ended("display-1")  # first → advance to round 2, play round2 on d1
    assert Play("display-1", "jason", Round.ROUND2, 0) in cmds1
    cmds2 = o.on_clip_ended("display-2")  # second → program already round2, no double-advance
    assert Play("display-2", "jason", Round.ROUND2, 1) in cmds2


def test_program_caps_at_round2_then_idles_no_round3():
    o = _orch(displays=("display-1",))
    o.on_identify("jason")                 # opener on d1
    o.on_clip_ended("display-1")           # → round 2 on d1
    cmds = o.on_clip_ended("display-1")    # round 2 ends → DONE → idle (NOT round 3)
    assert Idle("display-1") in cmds
    assert not any(isinstance(c, Play) for c in cmds)  # no round 3 play


def test_program_done_evicts_owner_renders_but_mid_program_does_not():
    # issue #27: the DONE boundary must tell the runtime to drop the owner's
    # cached renders; mid-program round advances must NOT (round 2 reuses the
    # render-ahead cache by design).
    o = _orch(displays=("display-1",))
    o.on_identify("jason")                       # opener on d1
    mid = o.on_clip_ended("display-1")           # → round 2 (mid-program)
    assert not any(isinstance(c, EvictRender) for c in mid)
    done = o.on_clip_ended("display-1")          # round 2 ends → DONE
    assert EvictRender("jason") in done


def test_clip_ended_for_unknown_display_returns_empty():
    # A kiosk connecting with a screen_id outside the configured displays must not
    # crash the handler — on_clip_ended returns [] rather than raising KeyError.
    o = _orch()
    assert o.on_clip_ended("display-99") == []


def test_new_identify_does_not_interrupt_playing_clips():
    o = _orch()
    o.on_identify("jason")           # jason opener on 1..4 (all playing)
    cmds = o.on_identify("maria")    # maria identified while jason mid-clip
    # nothing plays yet — all four are still playing jason's opener
    assert not any(isinstance(c, Play) for c in cmds)


def test_newest_wins_takes_freed_displays_at_boundaries():
    o = _orch()
    o.on_identify("jason")
    o.on_identify("maria")           # 2 active → split 2/2, maria newest
    # display-3 frees: maria (newest) owns 3,4 → her opener on the freed display-3
    cmds = o.on_clip_ended("display-3")
    play = next(c for c in cmds if isinstance(c, Play))
    assert play.owner == "maria"
    assert play.round == Round.OPENER


def test_presence_ttl_expiry_drops_owner_and_idles_on_next_boundary():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")               # present at t=0, opener on d1 (playing)
    clock.t = 3.0
    o.on_presence(["jason"])             # heartbeat refreshes last_seen=3.0
    clock.t = 10.0                       # >5s since last heartbeat → expired
    o.tick()                             # jason no longer present; d1 still playing → skipped
    cmds = o.on_clip_ended("display-1")  # clip ends → no active owner → idle
    assert Idle("display-1") in cmds


def test_presence_heartbeat_keeps_owner_active():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")
    clock.t = 4.0
    o.on_presence(["jason"])             # refresh
    clock.t = 6.0                        # only 2s since refresh → still present
    o.tick()
    cmds = o.on_clip_ended("display-1")  # ends opener → advances to round 2, still jason
    assert any(isinstance(c, Play) and c.owner == "jason" for c in cmds)


def test_remaining_active_person_reclaims_displays_after_other_leaves():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1", "display-2"], clock=clock, presence_ttl_s=5.0)
    o.on_identify("jason")    # t0: jason owns 1,2 (opener playing on both)
    clock.t = 1.0
    o.on_identify("maria")    # maria active; split 1/1 deferred to boundaries
    # jason leaves (never heartbeats again); only maria keeps heartbeating
    clock.t = 9.0
    o.on_presence(["maria"])  # maria fresh at t=9
    clock.t = 10.0
    o.tick()                  # jason (last seen t=0) expires; maria (t=9) stays
    # both displays end their clips → maria (only active) reclaims both
    o.on_clip_ended("display-1")
    cmds = o.on_clip_ended("display-2")
    owners = {c.owner for c in cmds if isinstance(c, Play)}
    assert owners == {"maria"}


# ---- abandoned-program TTL sweep (issue #36) ----


def test_abandoned_mid_round_program_swept_after_ttl_fresh_opener_on_return():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0: opener playing on d1
    clock.t = 901.0                      # 901 > 900 → abandoned
    cmds = o.tick()
    assert EvictRender("jason") in cmds
    assert "jason" not in o._programs    # program forgotten
    o.on_clip_ended("display-1")         # stale clip ends → display idles
    clock.t = 902.0
    cmds = o.on_identify("jason")        # returning visitor → fresh program
    assert Play("display-1", "jason", Round.OPENER, 0) in cmds
    assert o._programs["jason"].first_seen == 902.0


def test_return_within_ttl_resumes_round2_no_evict_and_restamps_window():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0: opener playing on d1
    clock.t = 7.0                        # jason's presence stale (>5s)
    o.tick()                             # presence expires (tick-loop equivalent)
    cmds = o.on_clip_ended("display-1")  # opener ends → program advances to ROUND2,
    assert Idle("display-1") in cmds     # but jason not present → display idles
    clock.t = 600.0                      # back within the 900s window
    cmds = o.on_identify("jason")
    assert not any(isinstance(c, EvictRender) for c in cmds)
    plays = [c for c in cmds if isinstance(c, Play)]
    assert plays and plays[0].round == Round.ROUND2   # resumes, no restart
    assert o._programs["jason"].last_present == 600.0  # window re-stamped
    # leave again → fresh full window measured from t=600, not t=0
    clock.t = 1499.0                     # 899s since last_present → still safe
    cmds = o.tick()
    assert not any(isinstance(c, EvictRender) for c in cmds)
    assert "jason" in o._programs


def test_return_after_sweep_gets_fresh_opener():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0: opener playing on d1
    clock.t = 901.0
    o.tick()                             # sweep: program forgotten
    o.on_clip_ended("display-1")         # stale clip ends → display idles
    clock.t = 960.0
    cmds = o.on_identify("jason")        # return after the sweep
    assert Play("display-1", "jason", Round.OPENER, 0) in cmds
    assert o._programs["jason"].round == Round.OPENER


def test_abandon_ttl_boundary_exactly_900_does_not_expire():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0)  # default TTL 900
    o.on_identify("jason")
    assert o._programs["jason"].last_present == 0.0  # initialized to first_seen
    clock.t = 900.0                      # exactly the TTL → strict >, NOT expired
    cmds = o.tick()
    assert not any(isinstance(c, EvictRender) for c in cmds)
    assert "jason" in o._programs


def test_heartbeating_person_never_expires_across_20_minutes():
    # Highest-value test: catches a missing last_present re-stamp in on_presence.
    # Heartbeat every 60s, then tick 10s later while the presence TTL (5s) has the
    # person OUT of _present — survival then depends solely on last_present.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")
    evicts = []
    for k in range(1, 21):               # 20 simulated minutes
        clock.t = 60.0 * k
        evicts += [c for c in o.on_presence(["jason"]) if isinstance(c, EvictRender)]
        clock.t = 60.0 * k + 10.0        # presence stale; only last_present protects
        evicts += [c for c in o.tick() if isinstance(c, EvictRender)]
    assert evicts == []
    assert "jason" in o._programs


def test_done_program_swept_after_ttl_without_second_evict():
    # DONE entries were already evicted at their boundary (issue #27); the sweep
    # must GC the leaked dict entry WITHOUT a duplicate EvictRender.
    clock = _Clock(0.0)
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")
    o.on_clip_ended("display-1")         # → ROUND2
    done = o.on_clip_ended("display-1")  # → DONE (EvictRender at the boundary)
    assert EvictRender("jason") in done
    clock.t = 901.0
    cmds = o.tick()
    assert "jason" not in o._programs    # leaked DONE entry GC'd
    assert not any(isinstance(c, EvictRender) for c in cmds)  # no duplicate


def test_abandon_ttl_policy_is_read_at_eval_time():
    # Flipping the policy value between ticks must change behavior WITHOUT
    # rebuilding the Orchestrator (env/config is read through the callable).
    clock = _Clock(0.0)
    ttl = {"value": 900.0}
    o = Orchestrator(["display-1"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: ttl["value"])
    o.on_identify("jason")
    clock.t = 100.0
    cmds = o.tick()                      # 100 < 900 → intact
    assert not any(isinstance(c, EvictRender) for c in cmds)
    ttl["value"] = 10.0                  # provider flips 900 → 10
    cmds = o.tick()                      # 100 > 10 → swept on the very next tick
    assert EvictRender("jason") in cmds
    assert "jason" not in o._programs


def test_sweep_is_per_subject_only_silent_person_evicted():
    clock = _Clock(0.0)
    o = Orchestrator(["display-1", "display-2"], clock=clock, presence_ttl_s=5.0,
                     abandon_ttl_s=lambda p: 900.0)
    o.on_identify("jason")               # t=0, then silent forever
    clock.t = 1.0
    o.on_identify("maria")
    for k in range(1, 4):                # maria heartbeats at t=300/600/900
        clock.t = 300.0 * k
        o.on_presence(["maria"])
    clock.t = 901.5                      # jason: 901.5s silent; maria: 1.5s
    cmds = o.tick()
    evicts = [c for c in cmds if isinstance(c, EvictRender)]
    assert evicts == [EvictRender("jason")]
    assert "jason" not in o._programs
    assert "maria" in o._programs
