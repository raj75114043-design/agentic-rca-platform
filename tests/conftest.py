import os
import tempfile

import pytest

_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update({"DATABASE_URL": f"sqlite:///{_DB}", "LLM_PROVIDER": "none", "API_KEYS": "test-key",
                   "SEED_SITES": "120"})

from rca.config import Settings  # noqa: E402
from rca.container import build  # noqa: E402


@pytest.fixture(scope="session")
def container():
    return build(Settings(), reseed=True)


@pytest.fixture(scope="session")
def labels(container):
    by = {}
    for l in container.repo.labels():
        by.setdefault(l["root_cause"], l)
    return by
