"""The "Wipe memory" button must work on Windows too, where a file can't be deleted while it's open
(the website sending a snapshot, an antivirus scanning a new one): run `pytest`."""

import os
import threading

import numpy as np
import pytest

from src import app

FRAME = np.zeros((480, 640, 3), np.uint8)
KEYS = (100, 100, 140, 130)
WALLET = (300, 200, 380, 260)


@pytest.fixture
def mem(tmp_path):
    m = app.Memory(str(tmp_path / "memory.db"), str(tmp_path / "snapshots"))
    yield m
    m.db.close()


def remember(m):
    m.observe("keys", KEYS, FRAME, 1000.0)
    m.observe("wallet", WALLET, FRAME, 1000.0)
    m.observe("keys", (300, 300, 340, 330), FRAME, 1005.0)  # moved: a second snapshot
    assert len(m.all()) == 2
    return snapshots(m)


def snapshots(m):
    return sorted(os.path.join(m.snap_dir, f) for f in os.listdir(m.snap_dir))


def test_wipe_empties_the_database_and_snapshots(mem):
    assert len(remember(mem)) == 3
    assert mem.wipe() == []
    assert mem.all() == []
    assert mem.db.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)
    assert snapshots(mem) == []
    # VACUUM: what was deleted isn't left in the file's free pages either
    with open(mem.db.execute("PRAGMA database_list").fetchone()[2], "rb") as f:
        assert b"wallet" not in f.read()


def test_wipe_survives_a_snapshot_open_for_a_moment(mem):
    """The Windows bug: os.remove() raised PermissionError (WinError 32) on a snapshot the
    website was sending, so the wipe stopped halfway and crashed the app."""
    snaps = remember(mem)
    held = open(snaps[0], "rb")
    threading.Timer(0.3, held.close).start()  # the website finishes sending it
    assert mem.wipe() == []
    assert mem.all() == []
    assert snapshots(mem) == []


def test_wipe_survives_a_snapshot_that_stays_open(mem):
    snaps = remember(mem)
    with open(snaps[0], "rb"):
        left = mem.wipe()  # never raises
        assert mem.all() == []  # the rows are gone even if a file isn't
        if os.name == "nt":
            assert left == [snaps[0]]
    assert mem.wipe() == []  # the next wipe gets it
    assert snapshots(mem) == []


def test_forget_survives_an_open_snapshot(mem):
    snaps = remember(mem)
    keys = [s for s in snaps if os.path.basename(s).startswith("keys_")]
    held = open(keys[0], "rb")
    threading.Timer(0.3, held.close).start()
    mem.forget("keys")
    assert [r["label"] for r in mem.all()] == ["wallet"]
    assert [os.path.basename(s).split("_")[0] for s in snapshots(mem)] == ["wallet"]


def test_app_wipe_forgets_what_it_is_following(mem):
    """The tracker's streaks must go too, or the next pass re-remembers it all at once."""
    a = app.App(mem, pointer=None)
    seen = [("keys", 0.9, KEYS), ("wallet", 0.9, WALLET)]
    t = 0.0
    while len(mem.all()) < 2:
        t += 0.5
        a.update(seen, FRAME, t)
        assert t < 10, "never remembered them"
    a.highlight = ("keys", t + 6)
    held = open(snapshots(mem)[0], "rb")
    threading.Timer(0.3, held.close).start()
    assert a.command("wipe memory", t) is True  # the command used to crash here on Windows
    assert mem.all() == [] and snapshots(mem) == []
    assert a.tracks == {} and a.tracker.tracks == [] and a.tracker.main == {}
    assert a.highlight is None
    t += 0.5
    a.update(seen, FRAME, t)  # still on the desk: seen again, but not remembered yet
    assert mem.all() == []
