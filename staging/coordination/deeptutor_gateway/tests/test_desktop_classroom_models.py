from __future__ import annotations

import math
import sys
from dataclasses import FrozenInstanceError
from itertools import pairwise

import pytest

from integrations.deeptutor_shchem_v1.desktop_classroom_models import (
    NEAR_EQUILIBRIUM_THRESHOLD,
    ReversibleFirstOrderModel,
)


def test_default_model_matches_analytical_solution_and_rate_law():
    model = ReversibleFirstOrderModel()
    assert model.total == 1.0
    assert model.equilibrium_a == 0.25
    assert model.equilibrium_b == 0.75
    assert model.relaxation_time == 2.5
    assert model.recommended_duration() == 15.0
    for t in (0.0, 0.001, 1.0, 5.0, 15.0):
        state = model.at(t)
        assert state.t == t
        assert state.a == pytest.approx(0.25 + 0.75 * math.exp(-0.4 * t))
        assert state.b == pytest.approx(1.0 - state.a)
        assert state.a + state.b == pytest.approx(model.total)
        assert state.forward_rate == pytest.approx(0.3 * state.a)
        assert state.reverse_rate == pytest.approx(0.1 * state.b)
        assert state.net_rate == state.forward_rate - state.reverse_rate
        assert state.relative_rate_gap == pytest.approx(math.exp(-0.4 * t))
        assert not state.dynamic_equilibrium
        assert state.regime == "approaching"


def test_equal_concentrations_do_not_indicate_equilibrium():
    model = ReversibleFirstOrderModel()
    state = model.at(math.log(3.0) / 0.4)
    assert state.a == pytest.approx(0.5)
    assert state.b == pytest.approx(0.5)
    assert state.forward_rate == pytest.approx(0.15)
    assert state.reverse_rate == pytest.approx(0.05)
    assert state.relative_rate_gap == pytest.approx(1.0 / 3.0)
    assert not state.near_equilibrium
    assert not state.dynamic_equilibrium
    assert state.regime == "approaching"


def test_dynamic_equilibrium_can_have_unequal_concentrations():
    model = ReversibleFirstOrderModel(a0=0.25, b0=0.75)
    for t in (0.0, 1e-20, 2.5, 100.0, sys.float_info.max):
        state = model.at(t)
        assert (state.a, state.b) == (0.25, 0.75)
        assert state.a != state.b
        assert state.forward_rate > 0.0
        assert state.forward_rate == pytest.approx(state.reverse_rate)
        assert state.net_rate == pytest.approx(0.0, abs=2e-17)
        assert state.dynamic_equilibrium
        assert state.near_equilibrium
        assert state.regime == "dynamic"


def test_near_equilibrium_uses_the_fixed_relative_rate_gap():
    model = ReversibleFirstOrderModel()
    assert NEAR_EQUILIBRIUM_THRESHOLD == 0.01
    before = model.at(math.log(50.0) / 0.4)
    after = model.at(math.log(200.0) / 0.4)
    assert before.relative_rate_gap == pytest.approx(0.02)
    assert not before.near_equilibrium
    assert after.relative_rate_gap == pytest.approx(0.005)
    assert after.near_equilibrium
    assert not after.dynamic_equilibrium
    scaled_model = ReversibleFirstOrderModel(a0=3.0)
    assert scaled_model.at(after.t).relative_rate_gap == pytest.approx(after.relative_rate_gap)


def test_rounded_asymptotic_plateau_does_not_claim_exact_dynamic_equilibrium():
    state = ReversibleFirstOrderModel().at(sys.float_info.max)
    assert (state.a, state.b) == (0.25, 0.75)
    assert state.forward_rate > 0.0 and state.reverse_rate > 0.0
    assert state.near_equilibrium
    assert not state.dynamic_equilibrium
    assert state.regime == "approaching"
    almost_equilibrated = ReversibleFirstOrderModel(a0=0.25 + 1e-15, b0=0.75 - 1e-15)
    assert not almost_equilibrated.at(sys.float_info.max).dynamic_equilibrium


def test_reverse_approach_and_nonzero_initial_concentrations():
    model = ReversibleFirstOrderModel(kf=0.2, kr=0.5, a0=0.1, b0=0.9)
    assert model.at(0.0).net_rate < 0.0
    for t in (0.0, 0.3, 4.0):
        state = model.at(t)
        expected = 5.0 / 7.0 + (0.1 - 5.0 / 7.0) * math.exp(-0.7 * t)
        assert state.a == pytest.approx(expected)
        assert state.a >= model.a0
        assert state.b <= model.b0
        assert state.a + state.b == pytest.approx(model.total)


def test_stationary_model_has_no_unique_equilibrium_and_no_dynamic_flag():
    model = ReversibleFirstOrderModel(kf=0.0, kr=0.0, a0=0.4, b0=0.6)
    assert model.equilibrium_a is None
    assert model.equilibrium_b is None
    assert model.relaxation_time is None
    assert model.recommended_duration() == 20.0
    for state in model.samples(20.0, 5):
        assert (state.a, state.b) == (0.4, 0.6)
        assert (state.forward_rate, state.reverse_rate, state.net_rate) == (0.0, 0.0, 0.0)
        assert state.relative_rate_gap == 0.0
        assert not state.dynamic_equilibrium
        assert not state.near_equilibrium
        assert state.regime == "stationary"


@pytest.mark.parametrize("kf,kr", [(0.3, 0.1), (0.0, 0.0), (0.3, 0.0), (0.0, 0.1)])
def test_empty_system_never_claims_dynamic_equilibrium(kf, kr):
    model = ReversibleFirstOrderModel(kf=kf, kr=kr, a0=0.0, b0=0.0)
    for t in (0.0, 1.0, sys.float_info.max):
        state = model.at(t)
        assert (state.a, state.b, state.forward_rate, state.reverse_rate) == (0.0, 0.0, 0.0, 0.0)
        assert state.relative_rate_gap == 0.0
        assert not state.dynamic_equilibrium
        assert not state.near_equilibrium
        assert state.regime == "empty"


@pytest.mark.parametrize(
    "kf,kr,a0,b0,expected_a,expected_b",
    [(0.3, 0.0, 1.0, 0.0, 0.0, 1.0), (0.0, 0.1, 0.0, 1.0, 1.0, 0.0),
     (0.3, 0.0, 0.0, 1.0, 0.0, 1.0), (0.0, 0.1, 1.0, 0.0, 1.0, 0.0)],
)
def test_one_way_limits_never_claim_dynamic_or_near_equilibrium(kf, kr, a0, b0, expected_a, expected_b):
    model = ReversibleFirstOrderModel(kf=kf, kr=kr, a0=a0, b0=b0)
    assert model.equilibrium_a == expected_a
    assert model.equilibrium_b == expected_b
    for t in (0.0, 0.1, 5.0, sys.float_info.max):
        state = model.at(t)
        assert state.a + state.b == pytest.approx(model.total)
        assert not state.dynamic_equilibrium
        assert not state.near_equilibrium
        assert state.regime == "one_way"
    assert model.at(sys.float_info.max).a == expected_a
    assert model.at(sys.float_info.max).b == expected_b


@pytest.mark.parametrize("name", ["kf", "kr", "a0", "b0"])
@pytest.mark.parametrize("invalid", [True, False, -1.0, math.nan, math.inf, -math.inf, "0.3", None, 1j])
def test_model_rejects_invalid_parameters(name, invalid):
    with pytest.raises(ValueError, match=name):
        ReversibleFirstOrderModel(**{name: invalid})


@pytest.mark.parametrize("invalid", [True, False, -1.0, math.nan, math.inf, -math.inf, "1", None, 1j])
def test_model_rejects_invalid_times_and_durations(invalid):
    model = ReversibleFirstOrderModel()
    with pytest.raises(ValueError, match="t"):
        model.at(invalid)
    with pytest.raises(ValueError, match="duration"):
        model.samples(invalid)


def test_samples_preserve_fixed_horizon_and_real_time_coordinates():
    model = ReversibleFirstOrderModel()
    states = model.samples(15.0)
    assert isinstance(states, tuple)
    assert len(states) == 241
    assert states[0].t == 0.0
    assert states[-1].t == 15.0
    assert states[80].t == 5.0
    assert states[160].t == 10.0
    assert all(left.t < right.t for left, right in pairwise(states))
    assert tuple(state.t for state in model.samples(9.0, 4)) == (0.0, 3.0, 6.0, 9.0)
    assert tuple(state.t for state in model.samples(0.0, 3)) == (0.0, 0.0, 0.0)
    huge = model.samples(sys.float_info.max, 4)
    assert huge[-1].t == sys.float_info.max
    assert all(math.isfinite(state.t) for state in huge)


@pytest.mark.parametrize("invalid", [True, False, 0, 1, -1, 2.0, "4", None])
def test_samples_reject_invalid_counts(invalid):
    with pytest.raises(ValueError, match="count"):
        ReversibleFirstOrderModel().samples(15.0, invalid)


def test_model_and_sample_are_immutable():
    model = ReversibleFirstOrderModel()
    state = model.at(0.0)
    with pytest.raises(FrozenInstanceError):
        model.kf = 0.4
    with pytest.raises(FrozenInstanceError):
        state.t = 1.0


def test_tiny_elapsed_time_preserves_product_formation():
    state = ReversibleFirstOrderModel().at(1e-20)
    assert state.b > 0.0
    assert state.b == pytest.approx(3e-21, rel=1e-14, abs=0.0)
    assert state.a + state.b == 1.0
    assert not state.dynamic_equilibrium


def test_rate_sum_overflow_does_not_overflow_the_analytical_solution():
    model = ReversibleFirstOrderModel(kf=1e308, kr=1e308, a0=2e-308)
    assert model.relaxation_time == pytest.approx(5e-309, rel=1e-14, abs=0.0)
    assert model.recommended_duration() == pytest.approx(3e-308, rel=1e-14, abs=0.0)
    state = model.at(5e-309)
    assert state.b == pytest.approx(1e-308 * (1.0 - math.exp(-1.0)), rel=1e-14, abs=0.0)
    assert state.a + state.b == pytest.approx(2e-308, rel=1e-14, abs=0.0)
    assert math.isfinite(state.forward_rate) and math.isfinite(state.reverse_rate)
    assert not state.dynamic_equilibrium


def test_extremely_slow_model_has_a_finite_recommended_observation_time():
    tiny_rate = math.ulp(0.0)
    model = ReversibleFirstOrderModel(kf=tiny_rate, kr=tiny_rate)
    assert model.relaxation_time == math.inf
    assert model.recommended_duration() == sys.float_info.max
    state = model.at(model.recommended_duration())
    assert state.b > 0.0
    assert state.b < 1e-14
    assert state.a + state.b == 1.0
    assert not state.dynamic_equilibrium


@pytest.mark.parametrize("reverse", [False, True])
def test_extreme_rate_ratio_retains_representable_tiny_equilibrium_species(reverse):
    tiny = math.ulp(0.0)
    kwargs = {"kf": 1e308, "kr": tiny, "a0": 0.0, "b0": 1e308}
    if reverse:
        kwargs = {"kf": tiny, "kr": 1e308, "a0": 1e308, "b0": 0.0}
    model = ReversibleFirstOrderModel(**kwargs)
    state = model.at(sys.float_info.max)
    assert min(state.a, state.b) == tiny
    assert max(state.a, state.b) == 1e308
    assert state.a + state.b == 1e308
    assert state.forward_rate > 0.0 and state.reverse_rate > 0.0
    assert state.forward_rate == pytest.approx(state.reverse_rate, rel=1e-14, abs=0.0)
    assert state.near_equilibrium
    assert not state.dynamic_equilibrium


def test_large_concentrations_remain_finite_and_conserved():
    model = ReversibleFirstOrderModel(kf=0.5, kr=0.5, a0=sys.float_info.max)
    for state in model.samples(20.0, 21):
        assert all(math.isfinite(value) for value in (
            state.a, state.b, state.forward_rate, state.reverse_rate,
            state.net_rate, state.relative_rate_gap,
        ))
        assert 0.0 <= state.a <= model.total
        assert 0.0 <= state.b <= model.total
        assert state.a + state.b == model.total


def test_unrepresentable_totals_or_reachable_rates_are_rejected():
    with pytest.raises(ValueError, match="total concentration"):
        ReversibleFirstOrderModel(a0=sys.float_info.max, b0=sys.float_info.max)
    with pytest.raises(ValueError, match="reachable reaction rates"):
        ReversibleFirstOrderModel(kf=1e308, a0=1e308)


def test_restart_at_a_sample_matches_the_same_analytical_trajectory():
    model = ReversibleFirstOrderModel(kf=0.7, kr=0.2, a0=0.4, b0=1.2)
    checkpoint = model.at(0.3)
    restarted = ReversibleFirstOrderModel(kf=model.kf, kr=model.kr, a0=checkpoint.a, b0=checkpoint.b)
    continued = restarted.at(0.7)
    original = model.at(1.0)
    assert continued.a == pytest.approx(original.a)
    assert continued.b == pytest.approx(original.b)
    assert continued.forward_rate == pytest.approx(original.forward_rate)
    assert continued.reverse_rate == pytest.approx(original.reverse_rate)
