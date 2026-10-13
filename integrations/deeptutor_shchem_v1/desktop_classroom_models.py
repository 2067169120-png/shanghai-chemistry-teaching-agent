"""Deterministic classroom model for closed, constant-volume A ⇌ B.

The 1:1 reaction is assumed to be first order in each direction at constant
temperature: d[A]/dt = -kf[A] + kr[B], with [A] + [B] = C0.  Rate constants
are in s^-1, concentrations in mol/L, and rates in mol/(L*s).  These are
model assumptions, rather than a rate law inferred from stoichiometry.

The analytical solution is evaluated without time stepping.  A finite-time
approach to equilibrium is kept distinct from an initially equilibrated
trajectory, even when floating-point concentrations reach their limiting
values.  Concentration equality is never an equilibrium criterion.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from numbers import Real

NEAR_EQUILIBRIUM_THRESHOLD = 0.01


def _finite_nonnegative(value: Real, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite nonnegative number")  # noqa: TRY004 - uniform UI validation contract
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite nonnegative number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return result


def _product_ratio(amount: float, rate: float, denominator: float, divisor: float = 1.0) -> float:
    """Evaluate amount * rate / denominator / divisor without early underflow."""
    if amount == 0.0 or rate == 0.0:
        return 0.0
    amount_mantissa, amount_exponent = math.frexp(amount)
    rate_mantissa, rate_exponent = math.frexp(rate)
    denominator_mantissa, denominator_exponent = math.frexp(denominator)
    mantissa = amount_mantissa * rate_mantissa / denominator_mantissa / divisor
    return math.ldexp(mantissa, amount_exponent + rate_exponent - denominator_exponent)


def _conserved_pair(total: float, smaller: float) -> tuple[float, float]:
    """Keep tiny components, correcting a complement's midpoint-rounding tie."""
    smaller = min(total, max(0.0, smaller))
    larger = total - smaller
    rounded_sum = smaller + larger
    if rounded_sum < total:
        smaller = math.nextafter(smaller, total)
    elif rounded_sum > total:
        smaller = math.nextafter(smaller, 0.0)
    return smaller, larger


@dataclass(frozen=True, slots=True)
class ReversibleFirstOrderState:
    """One sample, at its actual time in seconds.

    ``net_rate`` is vf - vr, so d[A]/dt = -net_rate.  The relative rate gap
    uses one fixed scale for the complete trajectory: max(initial vf,
    initial vr, limiting rate).  A zero scale yields a gap of 0.0 as a
    numeric placeholder; it never enables either equilibrium flag.
    """

    t: float
    a: float
    b: float
    forward_rate: float
    reverse_rate: float
    net_rate: float
    relative_rate_gap: float
    dynamic_equilibrium: bool
    near_equilibrium: bool
    regime: str


@dataclass(frozen=True, slots=True)
class ReversibleFirstOrderModel:
    """Analytical, reversible first-order A ⇌ B model.

    ``equilibrium_a`` and ``equilibrium_b`` are None when both rate
    constants vanish: every composition then remains stationary and the
    rate law supplies no unique limiting composition.  A single zero rate
    constant is a one-way limiting case, never dynamic equilibrium.

    Parameters must be finite and nonnegative.  Combinations whose total
    concentration or reachable rates exceed the floating-point range are
    rejected rather than allowing nonfinite states.  Extremely slow
    relaxation can have an infinite floating-point time constant; the
    recommended observation duration is still finite.
    """

    kf: float = 0.3
    kr: float = 0.1
    a0: float = 1.0
    b0: float = 0.0

    def __post_init__(self) -> None:
        for name in ("kf", "kr", "a0", "b0"):
            object.__setattr__(self, name, _finite_nonnegative(getattr(self, name), name))
        try:
            total = math.fsum((self.a0, self.b0))
        except OverflowError as exc:
            raise ValueError("total concentration must be finite") from exc
        if not math.isfinite(total):
            raise ValueError("total concentration must be finite")
        equilibrium = self._equilibrium_components()
        if equilibrium is not None:
            a_limit, b_limit = equilibrium
            reachable_rates = (
                self.kf * max(self.a0, a_limit),
                self.kr * max(self.b0, b_limit),
            )
            if not all(math.isfinite(rate) for rate in reachable_rates):
                raise ValueError("reachable reaction rates must be finite")

    @property
    def total(self) -> float:
        return math.fsum((self.a0, self.b0))

    def _equilibrium_components(self) -> tuple[float, float] | None:
        largest_rate = max(self.kf, self.kr)
        if largest_rate == 0.0:
            return None
        rate_sum = self.kf + self.kr
        if math.isfinite(rate_sum):
            denominator, divisor = rate_sum, 1.0
        else:
            # kf + kr may overflow although their fractions are well defined.
            denominator = largest_rate
            divisor = self.kf / largest_rate + self.kr / largest_rate
        # Calculate the smaller component directly to retain tiny amounts.
        # Its complement enforces conservation without cancellation in it.
        if self.kr <= self.kf:
            a = _product_ratio(self.total, self.kr, denominator, divisor)
            return _conserved_pair(self.total, a)
        b = _product_ratio(self.total, self.kf, denominator, divisor)
        b, a = _conserved_pair(self.total, b)
        return a, b

    @property
    def equilibrium_a(self) -> float | None:
        equilibrium = self._equilibrium_components()
        return equilibrium[0] if equilibrium is not None else None

    @property
    def equilibrium_b(self) -> float | None:
        equilibrium = self._equilibrium_components()
        return equilibrium[1] if equilibrium is not None else None

    @property
    def relaxation_time(self) -> float | None:
        largest_rate = max(self.kf, self.kr)
        if largest_rate == 0.0:
            return None
        rate_sum = self.kf + self.kr
        if math.isfinite(rate_sum):
            return 1.0 / rate_sum
        return (1.0 / largest_rate) / (self.kf / largest_rate + self.kr / largest_rate)

    def recommended_duration(self) -> float:
        """Return six time constants, or 20 s for a stationary model.

        Durations beyond the representable range saturate at the largest
        finite float, so the result is always usable as an observation time.
        """
        tau = self.relaxation_time
        if tau is None:
            return 20.0
        return min(6.0 * tau, sys.float_info.max)

    def at(self, t: float) -> ReversibleFirstOrderState:
        t = _finite_nonnegative(t, "t")
        total = self.total
        equilibrium = self._equilibrium_components()
        starts_at_equilibrium = equilibrium is not None and (self.a0, self.b0) == equilibrium
        if t == 0.0 or equilibrium is None or starts_at_equilibrium:
            a, b = self.a0, self.b0
        else:
            rate_sum = self.kf + self.kr
            if math.isfinite(rate_sum):
                elapsed = rate_sum * t
            else:
                largest_rate = max(self.kf, self.kr)
                elapsed = (largest_rate * t) * (self.kf / largest_rate + self.kr / largest_rate)
            remaining = math.exp(-elapsed)
            progressed = -math.expm1(-elapsed)
            a_limit, b_limit = equilibrium
            a_candidate = self.a0 * remaining + a_limit * progressed
            b_candidate = self.b0 * remaining + b_limit * progressed
            if a_candidate <= b_candidate:
                a, b = _conserved_pair(total, a_candidate)
            else:
                b, a = _conserved_pair(total, b_candidate)

        forward_rate, reverse_rate = self.kf * a, self.kr * b
        net_rate = forward_rate - reverse_rate
        initial_forward, initial_reverse = self.kf * self.a0, self.kr * self.b0
        limiting_rate = 0.0
        if equilibrium is not None:
            limiting_rate = max(self.kf * equilibrium[0], self.kr * equilibrium[1])
        rate_scale = max(initial_forward, initial_reverse, limiting_rate)
        relative_gap = abs(net_rate) / rate_scale if rate_scale > 0.0 else 0.0

        positive_reversible_rates = (
            self.kf > 0.0 and self.kr > 0.0
            and forward_rate > 0.0 and reverse_rate > 0.0
        )
        dynamic = positive_reversible_rates and starts_at_equilibrium
        near = (
            positive_reversible_rates and rate_scale > 0.0
            and relative_gap <= NEAR_EQUILIBRIUM_THRESHOLD
        )
        if total == 0.0:
            regime = "empty"
        elif equilibrium is None:
            regime = "stationary"
        elif self.kf == 0.0 or self.kr == 0.0:
            regime = "one_way"
        elif dynamic:
            regime = "dynamic"
        else:
            regime = "approaching"
        return ReversibleFirstOrderState(
            t=t, a=a, b=b, forward_rate=forward_rate, reverse_rate=reverse_rate,
            net_rate=net_rate, relative_rate_gap=relative_gap,
            dynamic_equilibrium=dynamic, near_equilibrium=near, regime=regime,
        )

    def samples(self, duration: float, count: int = 241) -> tuple[ReversibleFirstOrderState, ...]:
        """Sample a fixed [0, duration] horizon with real time coordinates."""
        duration = _finite_nonnegative(duration, "duration")
        if isinstance(count, bool) or not isinstance(count, int) or count < 2:
            raise ValueError("count must be an integer of at least 2")
        return tuple(self.at(duration * (index / (count - 1))) for index in range(count))
