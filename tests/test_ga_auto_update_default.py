"""GA patch: Supervisor auto_update defaults to False on fresh state only."""

from supervisor.validate import SCHEMA_UPDATER_CONFIG


def test_fresh_state_auto_update_off():
    """A fresh state (no persisted key) defaults auto_update to False (Odoo #1615)."""
    assert SCHEMA_UPDATER_CONFIG({})["auto_update"] is False


def test_persisted_true_stays_true():
    """A persisted True is never rewritten by the GA default."""
    assert SCHEMA_UPDATER_CONFIG({"auto_update": True})["auto_update"] is True


def test_persisted_false_stays_false():
    """A persisted False stays False."""
    assert SCHEMA_UPDATER_CONFIG({"auto_update": False})["auto_update"] is False
