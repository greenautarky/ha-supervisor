"""GA patch: Supervisor auto_update defaults to False on fresh state only."""

from supervisor.validate import SCHEMA_UPDATER_CONFIG


def test_fresh_state_auto_update_off():
    assert SCHEMA_UPDATER_CONFIG({})["auto_update"] is False


def test_persisted_true_stays_true():
    assert SCHEMA_UPDATER_CONFIG({"auto_update": True})["auto_update"] is True


def test_persisted_false_stays_false():
    assert SCHEMA_UPDATER_CONFIG({"auto_update": False})["auto_update"] is False
