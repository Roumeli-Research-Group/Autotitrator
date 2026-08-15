import io
import os
import zipfile

import pytest


class TestStatus:
    def test_status_shape(self, client):
        data = client.get('/api/status').get_json()
        for key in ('pump_status', 'running', 'ec', 'ph', 'temp', 'env',
                    'titration_error'):
            assert key in data
        assert data['env'] == 'DEV'

    def test_probe_read_valid_types(self, client):
        for probe in ('ph', 'ec', 'temp'):
            res = client.get(f'/api/probe_read/{probe}')
            assert res.status_code == 200
            assert res.get_json()['value'] is not None

    def test_probe_read_unknown_type(self, client):
        assert client.get('/api/probe_read/orp').status_code == 400


class TestInterlocks:
    def test_titration_blocked_while_measuring(self, client):
        from src.app import single_measurement
        single_measurement.running = True
        try:
            res = client.post('/api/titrate/start', json={'mode': 'volumetric'})
            assert res.status_code == 400
            assert 'measurement' in res.get_json()['error'].lower()
        finally:
            single_measurement.running = False

    def test_manual_pump_blocked_during_pulse_train(self, client):
        from src.app import pulse_runner
        pulse_runner.running = True
        try:
            assert client.post('/start_pump').status_code == 400
        finally:
            pulse_runner.running = False

    def test_stop_pump_always_allowed(self, client):
        from src.app import pulse_runner
        pulse_runner.running = True
        try:
            assert client.post('/stop_pump').status_code == 200
        finally:
            pulse_runner.running = False

    def test_calibration_blocked_while_busy(self, client):
        from src.app import engine
        engine.running = True
        try:
            res = client.post('/start_ph_calibration',
                              json={'type': 'mid', 'value': 7.0})
            assert res.status_code == 400
        finally:
            engine.running = False


class TestValidation:
    def test_measure_rejects_bad_sensor(self, client):
        res = client.post('/api/measure/start',
                          json={'sensor': 'orp', 'duration': 30})
        assert res.status_code == 400

    def test_measure_rejects_bad_duration(self, client):
        res = client.post('/api/measure/start',
                          json={'sensor': 'ec', 'duration': 2})
        assert res.status_code == 400

    def test_pulse_rejects_out_of_range(self, client):
        assert client.post('/api/pump/pulse',
                           json={'on_time': 0.001, 'pulses': 5}).status_code == 400
        assert client.post('/api/pump/pulse',
                           json={'on_time': 1, 'pulses': 9999}).status_code == 400

    def test_ph_calibration_type_whitelist(self, client):
        res = client.post('/start_ph_calibration', json={'type': 'bogus'})
        assert res.status_code == 400

    def test_rtd_calibration_requires_value(self, client):
        res = client.post('/start_rtd_calibration', json={'type': 'point'})
        assert res.status_code == 400

    def test_rtd_calibration_point_works(self, client):
        res = client.post('/start_rtd_calibration',
                          json={'type': 'point', 'value': 0.0})
        assert res.status_code == 200


class TestSettings:
    def test_unknown_key_rejected(self, client):
        res = client.post('/api/settings', json={'HACKED_KEY': 1})
        assert res.status_code == 400

    def test_round_trip(self, client):
        res = client.post('/api/settings', json={'VOLUME_PER_STEP': 0.25})
        assert res.status_code == 200
        assert client.get('/api/settings').get_json()['VOLUME_PER_STEP'] == 0.25

    def test_reset_restores_defaults(self, client):
        client.post('/api/settings', json={'VOLUME_PER_STEP': 0.25})
        assert client.post('/api/settings/reset').status_code == 200
        assert client.get('/api/settings').get_json()['VOLUME_PER_STEP'] == 0.5


class TestCalibrationDates:
    def test_ph_calibration_sets_only_ph_date(self, client):
        res = client.post('/start_ph_calibration',
                          json={'type': 'mid', 'value': 7.0})
        assert res.status_code == 200
        settings = client.get('/api/settings').get_json()
        assert settings['PH_CAL_DATE'] is not None
        assert settings['EC_CAL_DATE'] is None
        assert settings['RTD_CAL_DATE'] is None

    def test_clear_does_not_count_as_calibration(self, client):
        client.post('/start_ph_calibration', json={'type': 'clear'})
        assert client.get('/api/settings').get_json()['PH_CAL_DATE'] is None


class TestProbeHealth:
    def test_ph_health(self, client):
        h = client.get('/api/probe_health/ph').get_json()
        assert 'slope_acid_pct' in h
        assert 'cal_points' in h

    def test_unknown_probe(self, client):
        assert client.get('/api/probe_health/orp').status_code == 400


class TestTitrationFiles:
    @pytest.fixture
    def sample_csv(self, client):
        from src.app import app
        proj_dir = os.path.join(app.root_path, 'static', 'titrations', 'PyTest')
        os.makedirs(proj_dir, exist_ok=True)
        path = os.path.join(proj_dir, 'unit_endpoint_20260101_000000.csv')
        with open(path, 'w') as f:
            f.write("# Autotitrator run metadata\n"
                    "# titrant: 0.1 M NaOH\n"
                    "# solution_temp_c: 22.10\n"
                    "Volume (mL),pH,StdDev,Mode\n"
                    "0.0,3.1,0.01,pH\n"
                    "0.5,3.4,0.02,pH\n")
        yield 'PyTest/unit_endpoint_20260101_000000.csv'
        import shutil
        shutil.rmtree(proj_dir, ignore_errors=True)

    def test_csv_data_parses_meta_and_table(self, client, sample_csv):
        d = client.get(f'/api/titrations/data/{sample_csv}').get_json()
        assert d['meta']['titrant'] == '0.1 M NaOH'
        assert d['data']['Volume (mL)'] == [0.0, 0.5]
        assert d['data']['pH'] == [3.1, 3.4]

    def test_traversal_blocked(self, client):
        res = client.get('/api/titrations/data/..%2f..%2fapp.py')
        assert res.status_code in (400, 404)

    def test_export_zip(self, client, sample_csv):
        res = client.get('/api/titrations/export/PyTest')
        assert res.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(res.data))
        assert 'unit_endpoint_20260101_000000.csv' in zf.namelist()

    def test_export_missing_project(self, client):
        assert client.get('/api/titrations/export/NopeNope').status_code == 404


class TestPresets:
    def test_presets_recorded_and_capped(self, client, tmp_config):
        from src.app import _remember_titration_settings
        for i in range(7):
            _remember_titration_settings({'step_volume': 0.1 + i, 'mode': 'endpoint'})
        presets = client.get('/api/titrate/presets').get_json()
        assert len(presets) == 5
        assert presets[0]['step_volume'] == 6.1  # newest first

    def test_dedup(self, client, tmp_config):
        from src.app import _remember_titration_settings
        entry = {'step_volume': 0.5, 'mode': 'volumetric'}
        _remember_titration_settings(dict(entry))
        _remember_titration_settings(dict(entry))
        presets = client.get('/api/titrate/presets').get_json()
        assert presets.count(entry) == 1
