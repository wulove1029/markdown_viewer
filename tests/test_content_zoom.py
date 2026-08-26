import math

import pytest

from app.content_zoom import clamp_zoom_factor, step_zoom_factor


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1, 0.5),
        (0.5, 0.5),
        (1.17, 1.17),
        (3.0, 3.0),
        (9, 3.0),
        (math.nan, 1.0),
        (math.inf, 1.0),
        ("invalid", 1.0),
    ],
)
def test_clamp_zoom_factor(value, expected):
    assert clamp_zoom_factor(value) == pytest.approx(expected)


def test_step_zoom_factor_uses_familiar_discrete_stops():
    assert step_zoom_factor(1.0, 1) == pytest.approx(1.1)
    assert step_zoom_factor(1.0, 3) == pytest.approx(1.5)
    assert step_zoom_factor(1.17, 1) == pytest.approx(1.25)
    assert step_zoom_factor(1.17, -1) == pytest.approx(1.1)
    assert step_zoom_factor(0.5, 4) == pytest.approx(1.0)


def test_step_zoom_factor_clamps_and_skips_intermediate_reflows():
    assert step_zoom_factor(2.5, 20) == pytest.approx(3.0)
    assert step_zoom_factor(0.67, -20) == pytest.approx(0.5)
    assert step_zoom_factor(1.25, 0) == pytest.approx(1.25)
