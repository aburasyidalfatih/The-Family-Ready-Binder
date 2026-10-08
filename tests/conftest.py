"""Tes berjalan dalam mode uji coba: tanpa OpenAI dan tanpa posting sungguhan."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.update(
    FAKE_AI="true",
    DRY_RUN="true",
    DASHBOARD_USER="admin",
    DASHBOARD_PASSWORD="tes-password-kuat",
    DATA_DIR=tempfile.mkdtemp(prefix="autopost-test-"),
    AUTO_GENERATE_DAILY="false",
    PUBLIC_BASE_URL="https://autopost.example.com",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, main  # noqa: E402

AUTH = ("admin", "tes-password-kuat")


@pytest.fixture
def client():
    with TestClient(main.app, base_url="https://autopost.example.com") as c:
        yield c


@pytest.fixture
def post_id():
    from app import generator
    return generator.create_post(pillar="Caring for Aging Parents")
