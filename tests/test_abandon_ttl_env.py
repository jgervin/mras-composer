import main


def test_malformed_env_falls_back_to_default(monkeypatch, caplog):
    # A malformed PROGRAM_ABANDON_TTL_S must not raise inside every tick — the
    # helper falls back to the 900s default and logs one warning (review M1).
    monkeypatch.setenv("PROGRAM_ABANDON_TTL_S", "not-a-number")
    assert main._abandon_ttl_from_env() == 900.0
    assert any("PROGRAM_ABANDON_TTL_S" in r.message for r in caplog.records)


def test_valid_env_used_and_low_values_clamped_to_60(monkeypatch):
    monkeypatch.setenv("PROGRAM_ABANDON_TTL_S", "300")
    assert main._abandon_ttl_from_env() == 300.0
    # sanity clamp: near-zero misconfig must not evict-thrash a live demo
    monkeypatch.setenv("PROGRAM_ABANDON_TTL_S", "5")
    assert main._abandon_ttl_from_env() == 60.0
