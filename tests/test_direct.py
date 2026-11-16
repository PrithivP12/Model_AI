"""Verify direct execution uses actual conserved engines without an HTTP service."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from agrosim.direct import AgroSim
from agrosim.api.service import AgroSimService
from agrosim.api.settings import Settings
from agrosim.api.models import SimulationRequest
from agrosim.api.service import ServiceError
from test_api_release import sample, finance_for


def test_direct_matches_service_without_network(tmp_path, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError('No network is allowed for this controlled scenario')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setenv('AGROSIM_URL', 'http://unavailable.invalid:1')
    monkeypatch.setenv('AGROSIM_API_TOKEN', 'deliberately-invalid-short-token')
    engine = AgroSim()
    assert engine.crops()['count'] == 88
    request = sample()
    assert engine.validate(request)['valid']
    expected = AgroSimService(Settings(tmp_path)).run_simulation(SimulationRequest.model_validate(request))
    result = engine.simulate(request, output_dir=tmp_path/'saved')
    assert result['numerical_sha256'] == expected.numerical_sha256
    assert result['timeseries_url'] is None
    assert result['agriculture']['products'][0]['product_dry_kg_ha'] == pytest.approx(4218.638930668569)
    assert max(map(abs, result['agriculture']['conservation'].values())) < 1e-6
    assert json.loads((tmp_path/'saved/result.json').read_text()) == result
    assert all(Path(p).is_file() for p in result['artifacts'].values())
    with pytest.raises(FileExistsError):
        engine.simulate(request, output_dir=tmp_path/'saved')


@pytest.mark.parametrize('crop,selected',[('barley','legacy_experimental'),('apple','special_physiology')])
def test_direct_dispatch(crop,selected):
    result = AgroSim().simulate(sample(crop))
    assert result['selected_engine'] == selected
    assert result['timeseries_url'] is None and result['artifacts'] == {}
    assert max(map(abs, result['agriculture']['conservation'].values())) < 1e-6


def test_direct_finance_and_comparison(tmp_path):
    request = sample(finance=finance_for('corn','corn_grain'))
    other = deepcopy(request)
    other['scenario_id'] = 'alternate'
    result = AgroSim().compare({'scenarios':[request,other], 'ranking':'conditional_financial'}, output_dir=tmp_path/'comparison')
    assert result['comparison_basis']['matched_environment']
    assert len(result['conditional_ranking']) == 2
    for item in result['scenarios']:
        assert item['finance']['status'] == 'evaluated_conditional'
        assert item['timeseries_url'] is None
        assert Path(item['artifacts']['timeseries']).is_file()
    assert json.loads((tmp_path/'comparison/result.json').read_text()) == result


def test_cli_json_and_error():
    command=[sys.executable,'-m','agrosim.direct','simulate']
    reply=subprocess.run(command,input=json.dumps(sample()),capture_output=True,text=True,timeout=60)
    assert reply.returncode == 0, reply.stderr
    assert json.loads(reply.stdout)['timeseries_url'] is None
    failed=subprocess.run(command,input='{"unexpected": true}',capture_output=True,text=True,timeout=20)
    assert failed.returncode == 2
    assert json.loads(failed.stderr)['errors']


def test_missing_inputs_are_not_invented(tmp_path):
    with pytest.raises(ServiceError):
        AgroSim().simulate({'farm':sample()['farm']}, output_dir=tmp_path/'should-not-exist')
    assert not (tmp_path/'should-not-exist').exists()
