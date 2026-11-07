import pytest
from pydantic import ValidationError

from senos.contracts.models import Observation, Workload


def test_workload_rejects_unsafe_unit():
    with pytest.raises(ValidationError):
        Workload(name="editor", unit="editor.service; rm -rf /", role="interactive")


def test_workload_rejects_bad_name():
    with pytest.raises(ValidationError):
        Workload(name="My Editor", unit="editor.service", role="interactive")


def test_observation_rejects_out_of_range_cpu():
    with pytest.raises(ValidationError):
        Observation(workload_name="editor", cpu_percent=250, memory_mb=300)
