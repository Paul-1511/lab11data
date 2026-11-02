import os

import pytest

from pipeline.tracking import ENV_ENABLED


@pytest.fixture(scope="session", autouse=True)
def mlflow_disabled_by_default():
    """Las pruebas del pipeline no escriben en el almacén local de MLflow.

    Las pruebas de `test_tracking.py` lo activan con una carpeta temporal.
    """
    previous = os.environ.get(ENV_ENABLED)
    os.environ[ENV_ENABLED] = "0"
    yield
    if previous is None:
        os.environ.pop(ENV_ENABLED, None)
    else:
        os.environ[ENV_ENABLED] = previous
