"""
Small statistics helpers shared by the engines. Pure functions, standard
library only.
"""

import math

Z_95 = 1.96


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """
    Wilson score interval for `successes` out of `total` trials (95% with
    the default z). Unlike the plain "p plus or minus" interval it stays
    inside [0, 1] and behaves sensibly at 0 or total successes, which
    matters for mention rates measured on only a handful of answers.
    Returns None when total is 0: there is nothing to estimate.
    """
    if total <= 0:
        return None
    phat = successes / total
    denominator = 1 + z**2 / total
    center = (phat + z**2 / (2 * total)) / denominator
    margin = (z * math.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2))) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)
