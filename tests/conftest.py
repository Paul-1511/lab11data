import os

import pytest

from pipeline.publish import ENV_ENABLED as PUBLISH_ENABLED
from pipeline.tracking import ENV_ENABLED as MLFLOW_ENABLED


@pytest.fixture(scope="session", autouse=True)
def external_stores_disabled_by_default():
    """Las pruebas del pipeline no escriben en el almacén de MLflow ni en published/.

    `test_tracking.py` y `test_publish.py` activan cada uno el suyo con carpetas temporales.
    """
    previous = {name: os.environ.get(name) for name in (MLFLOW_ENABLED, PUBLISH_ENABLED)}
    for name in previous:
        os.environ[name] = "0"
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
