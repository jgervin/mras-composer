"""Schema-contract test: run the selector's REAL SQL against a migrated Postgres.

The selector unit tests mock ``db.fetchrow`` and dispatch on SQL substrings, so
schema drift (e.g. the dropped-``identities`` bug fixed in PR #28) stays
unit-green and only breaks live. This module builds a throwaway database on the
live PG16 dev server, applies ALL mras-ops God View migrations in filename
order, seeds ``subject_profiles`` rows, and exercises the real ``select()`` /
``select_variants()`` from /Users/jn/code/mras-composer/src/selector/selector.py
over a real asyncpg connection.

Pattern copied from /Users/jn/code/mras-ops/tests/test_schema_godview.py.

Requirements (module SKIPS cleanly when unmet — never breaks CI-less envs):
- migrations dir: ``MRAS_OPS_MIGRATIONS_DIR`` env var; default walks upward
  from this repo's root looking for a sibling ``mras-ops/db/migrations`` at
  each ancestor level (so git worktrees under ``.worktrees/`` resolve too)
- the dockerized Postgres running:
      cd /Users/jn/code/mras-ops && docker compose up -d postgres

Set ``MRAS_CONTRACT_REQUIRED=1`` to turn every skip into a hard failure
(for environments where this contract MUST run, e.g. a wired-up CI job).

Only *connection establishment* may skip. Once a connection succeeds, any
migration/seed error propagates as a test ERROR — schema drift must never
masquerade as "server unavailable".

Note: ``ads.base_video`` duplicating ``media_assets.storage_url`` is a
transitional dual source of truth (called out in
/Users/jn/code/mras-ops/db/migrations/014_creative.sql itself) — this suite is
the tripwire that must break when the selector migrates to ``media_assets``.
"""
import asyncio
import contextlib
import glob
import os
import pathlib
import uuid

import asyncpg
import pytest

from src.selector.selector import select, select_variants

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _default_migrations_dir() -> pathlib.Path:
    # Walk upward: a plain checkout finds /Users/jn/code/mras-ops next to the
    # repo; a worktree at <repo>/.worktrees/<name> finds it two levels up.
    for ancestor in _REPO_ROOT.parents:
        candidate = ancestor / "mras-ops" / "db" / "migrations"
        if candidate.is_dir():
            return candidate
    return _REPO_ROOT.parent / "mras-ops" / "db" / "migrations"


MIGRATIONS_DIR = pathlib.Path(
    os.environ.get("MRAS_OPS_MIGRATIONS_DIR", str(_default_migrations_dir()))
)
MIGRATIONS = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")))

ADMIN_DSN = os.environ.get("ADMIN_DATABASE_URL", "postgresql://mras:mras@localhost:5432/postgres")
# Randomized per test run so parallel agents/worktrees never collide on the
# throwaway DB (multi-agent-on-worktrees workflow).
TEST_DB = f"mras_composer_contract_{uuid.uuid4().hex[:8]}"
TEST_DSN = f"postgresql://mras:mras@localhost:5432/{TEST_DB}"

# Seeded subject_profiles rows (fixed UUIDs so every test agrees on them).
KNOWN_NAMED_UUID = "11111111-1111-4111-8111-111111111111"      # status='known', display_name set
ANONYMOUS_UUID = "22222222-2222-4222-8222-222222222222"        # status='anonymous', display_name set
KNOWN_NULL_NAME_UUID = "33333333-3333-4333-8333-333333333333"  # status='known', display_name NULL
ABSENT_UUID = str(uuid.uuid4())                                # never inserted

# Ad-catalog rows seeded per-test by the ``seeded_ad_catalog`` fixture (fixed
# UUIDs so assertions can name rows exactly; issue #37).
COMP_A_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"       # slug 'lower-third', status 'ready'
COMP_B_UUID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"       # slug 'side-banner', status 'ready'
COMP_X_UUID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"       # slug 'broken-bundle', status 'bundling'
AD_A_UUID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"         # eligible, newest eligible
AD_B_UUID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"         # eligible, migration-default props
AD_INACTIVE_UUID = "ffffffff-ffff-4fff-8fff-ffffffffffff"  # is_active=false (newest row overall)
AD_NOT_READY_UUID = "99999999-9999-4999-8999-999999999999" # active but component still 'bundling'


class _PgUnavailable(Exception):
    """Dev Postgres unreachable (connection-refused class) — the ONLY skippable condition."""


def _skip_or_fail(reason: str):
    if os.environ.get("MRAS_CONTRACT_REQUIRED") == "1":
        pytest.fail(f"MRAS_CONTRACT_REQUIRED=1 but contract cannot run: {reason}")
    pytest.skip(reason)


async def _connect(dsn: str) -> asyncpg.Connection:
    # Only the connect itself may convert into a SKIP. asyncpg.PostgresError is
    # deliberately NOT caught: once the server answers, errors (failed
    # migration, drifted column, bad seed) are real failures.
    try:
        return await asyncpg.connect(dsn, timeout=5)
    except (OSError, asyncio.TimeoutError) as exc:
        raise _PgUnavailable(f"cannot reach Postgres at {dsn}: {exc!r}") from exc


async def _setup_contract_db():
    admin = await _connect(ADMIN_DSN)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await admin.close()

    conn = await _connect(TEST_DSN)
    try:
        for path in MIGRATIONS:
            await conn.execute(pathlib.Path(path).read_text())
        await conn.execute(
            "INSERT INTO subject_profiles (id, status, display_name) VALUES "
            f"('{KNOWN_NAMED_UUID}', 'known', 'Ada Lovelace'), "
            f"('{ANONYMOUS_UUID}', 'anonymous', 'Shadow Person'), "
            f"('{KNOWN_NULL_NAME_UUID}', 'known', NULL)"
        )
    finally:
        await conn.close()


async def _drop_contract_db():
    admin = await _connect(ADMIN_DSN)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def contract_db():
    """Sync module fixture: builds the throwaway DB once via asyncio.run so it
    stays independent of pytest-asyncio's per-test event loops."""
    if not MIGRATIONS:
        _skip_or_fail(
            f"no mras-ops migrations found at {MIGRATIONS_DIR} (set MRAS_OPS_MIGRATIONS_DIR)"
        )
    try:
        asyncio.run(_setup_contract_db())
    except _PgUnavailable as exc:
        _skip_or_fail(str(exc))
    except BaseException:
        # Mid-setup failure (e.g. a drifted migration or failed seed) must
        # surface as a test ERROR, not a skip. Best-effort drop so the
        # randomized throwaway DB is not orphaned; suppressed because the
        # server itself may be what just broke.
        with contextlib.suppress(Exception):
            asyncio.run(_drop_contract_db())
        raise
    yield
    asyncio.run(_drop_contract_db())


@pytest.fixture
async def db(contract_db):
    conn = await asyncpg.connect(TEST_DSN)
    yield conn
    await conn.close()


def _trigger(person_uuid):
    return {"uuid": person_uuid, "is_new_visitor": False}


async def test_known_named_profile_personalizes(db):
    sel = await select(_trigger(KNOWN_NAMED_UUID), db)
    assert sel.type == "personalized"
    assert sel.person_name == "Ada Lovelace"
    assert sel.person_uuid == KNOWN_NAMED_UUID
    assert "Ada Lovelace" in sel.tts_text
    # No ads/components seeded: text-overlay fallback (the ads JOIN SQL still
    # executed against the real schema above).
    assert sel.overlay_text == "Ada Lovelace"


async def test_absent_uuid_falls_back_to_standard(db):
    sel = await select(_trigger(ABSENT_UUID), db)
    assert sel.type == "standard"
    assert sel.person_name is None


async def test_anonymous_status_falls_back_to_standard(db):
    # PR #28 guard: only status='known' personalizes, even if a name exists.
    sel = await select(_trigger(ANONYMOUS_UUID), db)
    assert sel.type == "standard"


async def test_null_display_name_falls_back_to_standard(db):
    # PR #28 guard: known row with NULL display_name must not greet "None".
    sel = await select(_trigger(KNOWN_NULL_NAME_UUID), db)
    assert sel.type == "standard"


async def test_select_variants_runs_real_variant_sql(db):
    # Exercises the ORDER BY random() LIMIT $1 variants query against the real
    # ads/components schema; zero active ads cycles the base selection.
    variants = await select_variants(_trigger(KNOWN_NAMED_UUID), db, 2)
    assert len(variants) == 2
    assert all(v.type == "personalized" for v in variants)
    assert all(v.person_name == "Ada Lovelace" for v in variants)


# ---------------------------------------------------------------------------
# Ad-bound path (issue #37): the seeded catalog below exercises the selector's
# real ads JOIN — eligibility predicates, ordering, and jsonb/uuid decoding.
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_ad_catalog(db):
    """Seed the ad catalog inside a per-test transaction, rolled back on
    teardown.

    WARNING for future editors: keep this seed function-scoped and rolled
    back — do NOT move it into the module-scoped ``contract_db`` fixture. The
    four zero-ads tests above depend on an EMPTY ads/components catalog (they
    assert the text-overlay fallback).
    """
    tr = db.transaction()
    await tr.start()
    yield {
        "COMP_A": COMP_A_UUID,
        "COMP_B": COMP_B_UUID,
        "COMP_X": COMP_X_UUID,
        "AD_A": AD_A_UUID,
        "AD_B": AD_B_UUID,
        "AD_INACTIVE": AD_INACTIVE_UUID,
        "AD_NOT_READY": AD_NOT_READY_UUID,
    }
    await tr.rollback()


async def test_select_returns_newest_eligible_ad(db, seeded_ad_catalog):
    # The two INELIGIBLE ads are seeded NEWEST, so this single equality proves
    # ORDER BY created_at DESC + is_active exclusion + status='ready'
    # exclusion at once — plus real-jsonb decode and uuid stringification.
    sel = await select(_trigger(KNOWN_NAMED_UUID), db)
    assert sel.type == "personalized"
    assert sel.ad_id == AD_A_UUID
    assert sel.component_id == COMP_A_UUID
    assert sel.composition_id == "comp-lower-third"
    assert sel.base_video == pathlib.Path("/assets/standard.mp4")
    assert sel.overlay_props == {"logo": "acme", "name": "Ada Lovelace"}


async def test_select_variants_filters_and_cycles(db, seeded_ad_catalog):
    # Set-based assertions only: the variants query is ORDER BY random().
    two = await select_variants(_trigger(KNOWN_NAMED_UUID), db, 2)
    assert {v.composition_id for v in two} == {"comp-lower-third", "comp-side-banner"}
    two_ad_ids = {v.ad_id for v in two}
    assert AD_INACTIVE_UUID not in two_ad_ids
    assert AD_NOT_READY_UUID not in two_ad_ids

    five = await select_variants(_trigger(KNOWN_NAMED_UUID), db, 5)
    assert len(five) == 5
    assert {v.ad_id for v in five} == {AD_A_UUID, AD_B_UUID}


async def test_ads_schema_defaults_flow_through_selector(db, seeded_ad_catalog):
    # AD_B is INSERTed without default_props/personalized_field: the migration
    # DEFAULTs ('{}'::jsonb / 'text') are selector contract for minimal rows.
    variants = await select_variants(_trigger(KNOWN_NAMED_UUID), db, 2)
    ad_b = next((v for v in variants if v.ad_id == AD_B_UUID), None)
    assert ad_b is not None, f"AD_B missing from variants: {[v.ad_id for v in variants]}"
    assert ad_b.overlay_props == {"text": "Ada Lovelace"}
