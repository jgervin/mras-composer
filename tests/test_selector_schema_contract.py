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
- migrations dir: ``MRAS_OPS_MIGRATIONS_DIR`` env var, default
  ``../mras-ops/db/migrations`` resolved relative to this repo's root
- the dockerized Postgres running:
      cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import asyncio
import glob
import os
import pathlib
import uuid

import asyncpg
import pytest

from src.selector.selector import select, select_variants

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = pathlib.Path(
    os.environ.get("MRAS_OPS_MIGRATIONS_DIR", str(_REPO_ROOT.parent / "mras-ops" / "db" / "migrations"))
)
MIGRATIONS = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")))

ADMIN_DSN = os.environ.get("ADMIN_DATABASE_URL", "postgresql://mras:mras@localhost:5432/postgres")
TEST_DB = "mras_composer_contract_test"
TEST_DSN = "postgresql://mras:mras@localhost:5432/" + TEST_DB

# Seeded subject_profiles rows (fixed UUIDs so every test agrees on them).
KNOWN_NAMED_UUID = "11111111-1111-4111-8111-111111111111"      # status='known', display_name set
ANONYMOUS_UUID = "22222222-2222-4222-8222-222222222222"        # status='anonymous', display_name set
KNOWN_NULL_NAME_UUID = "33333333-3333-4333-8333-333333333333"  # status='known', display_name NULL
ABSENT_UUID = str(uuid.uuid4())                                # never inserted

_PG_ERRORS = (OSError, asyncio.TimeoutError, asyncpg.PostgresError)


async def _setup_contract_db():
    admin = await asyncpg.connect(ADMIN_DSN, timeout=5)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await admin.close()

    conn = await asyncpg.connect(TEST_DSN)
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
    admin = await asyncpg.connect(ADMIN_DSN, timeout=5)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def contract_db():
    """Sync module fixture: builds the throwaway DB once via asyncio.run so it
    stays independent of pytest-asyncio's per-test event loops."""
    if not MIGRATIONS:
        pytest.skip(f"no mras-ops migrations found at {MIGRATIONS_DIR} (set MRAS_OPS_MIGRATIONS_DIR)")
    try:
        asyncio.run(_setup_contract_db())
    except _PG_ERRORS as exc:
        pytest.skip(f"Postgres dev server unavailable at {ADMIN_DSN}: {exc!r}")
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
