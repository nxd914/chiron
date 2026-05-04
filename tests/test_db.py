"""Tests for core/db.py — WAL connect helper."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.db import connect


def test_connect_returns_connection(tmp_path):
    """connect() should return a working sqlite3.Connection."""
    db = tmp_path / "test.db"
    conn = connect(str(db))
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_wal_mode_is_set(tmp_path):
    """journal_mode must be WAL after connect()."""
    db = tmp_path / "test.db"
    conn = connect(str(db))
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn.close()


def test_synchronous_normal(tmp_path):
    """synchronous pragma must be NORMAL (1) for crash-safe performance."""
    db = tmp_path / "test.db"
    conn = connect(str(db))
    row = conn.execute("PRAGMA synchronous").fetchone()
    assert row[0] == 1  # NORMAL
    conn.close()


def test_wal_persists_after_reconnect(tmp_path):
    """WAL mode must survive a reconnect — it's stored in the DB file."""
    db = tmp_path / "test.db"
    conn = connect(str(db))
    conn.close()

    # Open with plain sqlite3 — should still be WAL
    conn2 = sqlite3.connect(str(db))
    row = conn2.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn2.close()


def test_kwargs_forwarded(tmp_path):
    """check_same_thread and other kwargs should be forwarded to sqlite3.connect."""
    db = tmp_path / "test.db"
    conn = connect(str(db), check_same_thread=False)
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_basic_read_write(tmp_path):
    """Connections returned by connect() must support normal DDL/DML."""
    db = tmp_path / "test.db"
    conn = connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    row = conn.execute("SELECT x FROM t").fetchone()
    assert row[0] == 42
    conn.close()
