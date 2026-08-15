import os
import sys
import json

# Force mock hardware before anything imports the app
os.environ['TITRATOR_ENV'] = 'DEV'

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Isolate config.json so tests never touch the real one."""
    import src.utils as utils
    cfg_file = tmp_path / 'config.json'
    cfg_file.write_text(json.dumps(dict(utils.DEFAULT_SETTINGS)))
    monkeypatch.setattr(utils, 'CONFIG_FILE', str(cfg_file))
    monkeypatch.setattr(utils, 'config', json.loads(cfg_file.read_text()))
    monkeypatch.setattr(utils, '_config_mtime', os.path.getmtime(str(cfg_file)))
    return cfg_file


@pytest.fixture
def client(tmp_config):
    """Flask test client running against mock hardware."""
    from src.app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
