"""Regression: record_credits must stamp owner_id so a session that receives
its first credit event BEFORE save_session() runs does not leak to other
users via the NULL-owner legacy rule.

See agent_store.record_credits / list_sessions / _owner_ok.
"""


def test_record_credits_stamps_owner_on_new_session(store):
    """record_credits is called from app.py inside the SSE loop, BEFORE
    save_session runs at stream end. If the row is created with
    owner_id=NULL, list_sessions(owner_id=other) would surface it because
    NULL is treated as legacy-visible."""
    store.record_credits("rc_new", 0.5, owner_id="alice_uid")

    assert store.session_owner("rc_new") == "alice_uid"

    # Bob must NOT see alice's brand-new session.
    bob_sids = {s["sid"] for s in store.list_sessions(owner_id="bob_uid")}
    assert "rc_new" not in bob_sids

    # Alice still sees her own.
    alice_sids = {s["sid"] for s in store.list_sessions(owner_id="alice_uid")}
    assert "rc_new" in alice_sids


def test_record_credits_preserves_existing_owner(store):
    """Repeated credit events must never change the owner of an already-owned
    session, even if the caller passes owner_id=None (e.g. anonymous
    re-entry)."""
    store.save_session("rc_keep", [], "m", title="t", owner_id="ownerA")
    store.record_credits("rc_keep", 1.0, owner_id="ownerA")
    store.record_credits("rc_keep", 2.0, owner_id=None)  # must not clobber
    store.record_credits("rc_keep", 3.0, owner_id="ownerB")  # must not steal
    assert store.session_owner("rc_keep") == "ownerA"


def test_record_credits_legacy_row_then_save_session_claims(store):
    """Back-compat: a row created with owner_id=None (legacy / anonymous
    first event) can still be claimed by save_session later — the existing
    claim path must keep working."""
    store.record_credits("rc_legacy", 0.3)  # no owner
    assert store.session_owner("rc_legacy") is None

    store.save_session("rc_legacy", [], "m", title="t", owner_id="claimer")
    assert store.session_owner("rc_legacy") == "claimer"


def test_record_credits_daily_bookkeeping_unchanged(store):
    """Sanity: the fix must not change credit accounting."""
    before_total = store.get_today_credits()
    before_user = store.get_user_today_credits("rc_acct_user")

    store.record_credits("rc_acct", 3.0, owner_id="rc_acct_user")
    assert abs(store.get_today_credits() - before_total - 3.0) < 1e-6
    assert abs(store.get_user_today_credits("rc_acct_user") - before_user - 3.0) < 1e-6

    # Second call with higher absolute total → delta bumps daily by the diff.
    store.record_credits("rc_acct", 4.5, owner_id="rc_acct_user")
    assert abs(store.get_today_credits() - before_total - 4.5) < 1e-6

    # Re-submitting the same absolute value adds nothing.
    store.record_credits("rc_acct", 4.5, owner_id="rc_acct_user")
    assert abs(store.get_today_credits() - before_total - 4.5) < 1e-6
