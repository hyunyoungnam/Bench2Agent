"""Small, testable rules used when a candidate revision is compared with the
previous one (data/work/verify_candidate_v2.py)."""
from __future__ import annotations

import collections


def label_origins_match(previous_extra: dict, new_extra: dict, added_origins: list[set[str]]) -> bool:
    """An edition's label-origin counts may change only by the label origins of
    the papers added to it: new == previous + one per origin per added paper,
    and nothing else in `extra` changes. A relabelled existing paper, or a
    wrong increase, fails."""
    want = collections.Counter(previous_extra.get("label_origins") or {})
    for origins in added_origins:
        want.update(collections.Counter(set(origins)))
    rest_old = {k: v for k, v in previous_extra.items() if k != "label_origins"}
    rest_new = {k: v for k, v in new_extra.items() if k != "label_origins"}
    return rest_old == rest_new and dict(want) == dict(new_extra.get("label_origins") or {})
