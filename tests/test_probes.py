from unittest.mock import patch

import pytest

from src.hardware.probes import MockProbe, RealAtlasProbe, TemperatureProbe


class TestParseResponse:
    def parse(self, response):
        # _parse_response doesn't use instance state beyond logging
        return RealAtlasProbe._parse_response(object.__new__(RealAtlasProbe), response)

    def test_success_with_colon(self):
        assert self.parse("Success 99 pH Probe: 7.005") == pytest.approx(7.005)

    def test_error_returns_zero(self):
        assert self.parse("Error 99 pH Probe: 254") == 0.0

    def test_null_bytes_stripped(self):
        assert self.parse("Success x: 1013.2\x00\x00") == pytest.approx(1013.2)

    def test_garbage_returns_zero(self):
        assert self.parse("Success x: not-a-number") == 0.0

    def test_none_returns_zero(self):
        assert self.parse(None) == 0.0


class TestMockProbe:
    def test_ph_stays_in_range(self):
        p = MockProbe("PH")
        for _ in range(500):
            assert 0.0 <= p.read() <= 14.0

    def test_temp_profile(self):
        p = MockProbe("TEMP")
        v = p.read()
        assert 15 < v < 30  # starts near 22 with small steps

    def test_health_shapes(self):
        assert 'slope_acid_pct' in MockProbe("PH").get_health()
        assert 'k_value' in MockProbe("EC").get_health()
        assert MockProbe("TEMP").get_health()['cal_points'] == 1

    def test_temp_compensation_accepted(self):
        assert "Success" in MockProbe("EC").set_temp_compensation(23.4)


class TestTemperatureProbe:
    def _bare(self):
        return object.__new__(TemperatureProbe)

    def test_no_sensor_sentinel_filtered(self):
        with patch.object(RealAtlasProbe, 'read', return_value=-1023.0):
            assert self._bare().read() is None

    def test_normal_reading_passes(self):
        with patch.object(RealAtlasProbe, 'read', return_value=21.5):
            assert self._bare().read() == 21.5

    def test_none_passthrough(self):
        with patch.object(RealAtlasProbe, 'read', return_value=None):
            assert self._bare().read() is None
