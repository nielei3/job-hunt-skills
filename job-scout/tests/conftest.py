from pathlib import Path

import pytest


@pytest.fixture
def project_root(tmp_path):
    """A throwaway project root with config/, data/inbox/, vault dirs."""
    (tmp_path / 'config').mkdir()
    (tmp_path / 'data' / 'inbox').mkdir(parents=True)
    (tmp_path / 'vault' / 'Career' / 'Jobs' / 'Daily Reports').mkdir(parents=True)
    (tmp_path / 'vault' / 'Career' / 'Jobs' / 'Opportunities').mkdir(parents=True)
    return tmp_path


@pytest.fixture
def fixtures_dir():
    return Path(__file__).resolve().parent / 'fixtures'
