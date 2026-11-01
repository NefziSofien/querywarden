"""Small-sample statistics, so a report can say when a difference is not clear."""

import math


def wilson_interval(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a proportion, which stays sensible with few cases and at 0% or 100%."""
    if total == 0:
        return (0.0, 1.0)
    rate = passed / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    # Exact at the edges, where rounding would otherwise give values like 0.9999999.
    low = 0.0 if passed == 0 else max(0.0, centre - margin)
    high = 1.0 if passed == total else min(1.0, centre + margin)
    return (low, high)


def mcnemar_exact(regressed: int, improved: int) -> float:
    """Two-sided p-value on paired results. Only cases whose outcome changed carry information."""
    changed = regressed + improved
    if changed == 0:
        return 1.0
    tail = sum(math.comb(changed, k) for k in range(min(regressed, improved) + 1))
    return min(1.0, 2 * tail / 2**changed)
