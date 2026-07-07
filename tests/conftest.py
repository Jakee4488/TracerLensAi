import pytest
from fastapi.testclient import TestClient
import sqlite3
import os

from src.main import app, lifespan

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch, tmp_path):
    """Override the database path to use a temporary file for tests."""
    test_db_path = tmp_path / "test_app.db"
    monkeypatch.setattr("src.database.DB_PATH", str(test_db_path))
    
    # Initialize the test database
    from src.database import init_db
    monkeypatch.setattr("src.database.DB_DIR", str(tmp_path))
    init_db()
    
    yield
