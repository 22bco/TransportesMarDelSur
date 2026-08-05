"""Fixtures compartidas.

La base de datos de los tests es un archivo real en `tmp_path`, no `:memory:`.
Dos razones: los triggers y `PRAGMA foreign_keys` son parte de lo que se
testea, y una base en memoria no comparte estado entre conexiones, que es
justo el patrón que usa get_db().
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def app(tmp_path):
    from app import crear_app
    return crear_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
        'DB_PATH': tmp_path / 'test.db',
    })


@pytest.fixture
def cliente_http(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    from db import get_db
    with app.app_context(), get_db() as c:
        yield c
