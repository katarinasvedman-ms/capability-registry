import importlib
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("azure.identity")
pytest.importorskip("jwt")


@pytest.mark.parametrize(
    ("name", "phase", "spike_class"),
    [
        ("tools.h9_spike", "approved", "Spike"),
        ("tools.h9_access", "readonly", "Spike"),
        ("tools.h9_policies", "native", "Spike"),
        ("tools.h9b_spike", "viewer", "H9b"),
        ("tools.h9b_live", "suite", "H9b"),
    ],
)
def test_expired_spike_stops_before_credentials_or_cloud_work(
    monkeypatch, name, phase, spike_class
):
    module = importlib.import_module(name)

    class ExpiredSpike:
        def check_time(self):
            raise TimeoutError("H9 spike time box exhausted")

    monkeypatch.setattr(module, spike_class, ExpiredSpike)
    monkeypatch.setattr(sys, "argv", [name, phase, "--live"])
    with pytest.raises(TimeoutError, match="time box exhausted"):
        module.main()
