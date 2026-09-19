"""compute_desired_replicas: desired = clamp(ceil(load/target), min, max)."""
from __future__ import annotations

from scaler import compute_desired_replicas


def test_zero_load_clamps_to_min_replicas():
    assert compute_desired_replicas(load=0, target_concurrency=2, min_replicas=1, max_replicas=4) == 1


def test_matches_the_monitor_screen_worked_example():
    # "load: 8, target: 2, desired: ceil(8/2) = 4, clamped to max 4"
    assert compute_desired_replicas(load=8, target_concurrency=2, min_replicas=1, max_replicas=4) == 4


def test_ceils_uneven_division():
    assert compute_desired_replicas(load=5, target_concurrency=2, min_replicas=0, max_replicas=10) == 3


def test_clamps_to_max_replicas_even_under_heavier_load():
    assert compute_desired_replicas(load=100, target_concurrency=1, min_replicas=0, max_replicas=4) == 4


def test_zero_target_concurrency_is_treated_as_one_not_a_division_error():
    assert compute_desired_replicas(load=3, target_concurrency=0, min_replicas=0, max_replicas=10) == 3


def test_min_replicas_floor_applies_even_with_zero_load():
    assert compute_desired_replicas(load=0, target_concurrency=1, min_replicas=2, max_replicas=5) == 2
