from __future__ import annotations

import pytest

from cecl import environment
from scripts import export_site_data


@pytest.mark.parametrize("name", ["local", "ci", "dev"])
def test_only_prod_exports(monkeypatch, name):
    monkeypatch.setenv("CECL_ENV", name)
    with pytest.raises(environment.ProdWriteRefused):
        export_site_data.main()


def test_prod_exports(monkeypatch, capsys):
    monkeypatch.setenv("CECL_ENV", "prod")
    assert export_site_data.main() == 0
    assert "placeholder" in capsys.readouterr().out
