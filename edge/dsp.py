"""Signal processing: outlier rejection, compensation and Brown WQI."""

from __future__ import annotations

from collections import deque
from statistics import median, pstdev

from .models import Reading

_WINDOW = 9
_K = 3.0
_MAD_SCALE = 1.4826
_R_J_PER_MOL_K = 8.314462618
_F_C_PER_MOL = 96485.33212
_TDS_ALPHA_PER_C = 0.02
_PH_NEUTRAL = 7.0
_WQI_S = (8.5, 5.0, 1000.0)
_WQI_V0 = (7.0, 0.0, 0.0)
_WQI_WEIGHTS = (0.3692, 0.6277, 0.0031)


class HampelFilter:
    """Replace isolated outliers with the median of the recent raw series."""

    def __init__(self, window: int = _WINDOW, k: float = _K) -> None:
        if window < 3:
            raise ValueError("window must be at least 3")
        self._window: deque[float] = deque(maxlen=window)
        self._k = float(k)

    def update(self, value: float) -> float:
        """Filter one sample, retaining raw values so a genuine step survives."""
        value = float(value)
        self._window.append(value)
        centre = median(self._window)
        sigma = _MAD_SCALE * median(abs(x - centre) for x in self._window)
        if sigma == 0.0:
            # A constant run has MAD=0; population stdev still exposes a lone spike.
            sigma = pstdev(self._window)
        if sigma > 0.0 and abs(value - centre) > self._k * sigma:
            return centre
        return value


def nernst_slope(temp_c: float) -> float:
    """Electrode slope in mV per pH at ``temp_c`` (59.16 at 25 °C)."""
    return 2.303 * _R_J_PER_MOL_K * (temp_c + 273.15) / _F_C_PER_MOL * 1000.0


def compensate_ph(raw_ph: float, temp_c: float, e7: float = 0.0) -> tuple[float, float]:
    """Return ``(pH_T, pH_25)`` for an electrode deviation from pH 7.

    ``raw_ph`` is the measured deviation from neutrality in pH units: with
    ``e7`` as the electrode offset in mV, ``E - E7 = e7 - s(T) * raw_ph``.
    The default ``e7=0`` reduces to ``7 - e/s(T)`` and ``7 - e/s(25)``.
    """
    slope = nernst_slope(temp_c)
    e = e7 - slope * raw_ph
    return _PH_NEUTRAL - e / slope, _PH_NEUTRAL - e / nernst_slope(25.0)


def ambient_tds(tds_25: float, temp_c: float) -> float:
    """Convert a 25 °C-equivalent TDS reading to the ambient measurement."""
    return tds_25 * (1.0 + _TDS_ALPHA_PER_C * (temp_c - 25.0))


def compensate_tds(raw_tds: float, temp_c: float) -> float:
    """Compensate an ambient TDS measurement back to 25 °C."""
    return raw_tds / (1.0 + _TDS_ALPHA_PER_C * (temp_c - 25.0))


def brown_wqi(ph: float, turbidity_ntu: float, tds_ppm: float) -> tuple[float, str]:
    """Brown WQI with no-chlorine fallback weights and its quality band."""
    subindices = [
        max(0.0, 100.0 * (value - v0) / (standard - v0))
        for value, v0, standard in zip(
            (ph, turbidity_ntu, tds_ppm), _WQI_V0, _WQI_S
        )
    ]
    wqi = sum(q * weight for q, weight in zip(subindices, _WQI_WEIGHTS))
    if wqi <= 25.0:
        band = "Excellent"
    elif wqi <= 50.0:
        band = "Good"
    elif wqi <= 75.0:
        band = "Poor"
    elif wqi <= 100.0:
        band = "Very poor"
    else:
        band = "Unsuitable"
    return wqi, band


def process_reading(reading: Reading, filters: dict[str, HampelFilter]) -> Reading:
    """Filter numeric channels, compensate pH/TDS and add WQI audit keys."""
    values = dict(reading.values)
    for channel, hampel in filters.items():
        if channel in values:
            values[channel] = hampel.update(values[channel])

    temp_c = values["water_temp_c"]
    values["ph"], values["ph_25"] = compensate_ph(
        values["ph"] - _PH_NEUTRAL, temp_c
    )
    values["tds_ppm"] = compensate_tds(values["tds_ppm"], temp_c)
    values["wqi"], values["wqi_band"] = brown_wqi(
        values["ph_25"], values["turbidity_ntu"], values["tds_ppm"]
    )
    return Reading(node_id=reading.node_id, ts_ms=reading.ts_ms, values=values)
