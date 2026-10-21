"""Display units shared by the benchmark tools and answer verification."""
from decimal import Decimal, ROUND_HALF_UP


def percentage(papers: int | float | None, denominator: int | float | None, *, places: int | None = 1) -> float | None:
    """Percent of the SAME analyzed scope; missing/zero denominators are unavailable.

    Tools display one decimal, rounding halves up as in the browser. The
    verifier can use the unrounded ratio from the turn's own counts.
    """
    if papers is None or denominator is None or denominator <= 0:
        return None
    value = Decimal(str(papers)) * 100 / Decimal(str(denominator))
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return float(value)
