import pytest
from fastapi.testclient import TestClient
import os

from src.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
