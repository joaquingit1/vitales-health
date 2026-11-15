"""Exposure settling before analysis. Most cameras adjust their exposure and white balance when they are opened: the
level of every pixel ramps for about a second. That ramp is much larger than the colour change of the pulse, so a
window that starts at the first frame can turn a still face in good light into "not measured" (found on a real laptop
camera; the measured ramp is in the quality policy's capture.settled_change_per_s basis). No frame counts until the
picture has settled: the whole frame mean of every colour channel changes by no more than the policy limit per
second, compared with the frame one second earlier.
"""

from __future__ import annotations

from collections import deque

LOOKBACK_NS = 1_000_000_000  # "per second": each frame is compared with the frame one second before it

type Means = tuple[float, float, float]


class Settling:
    """Fed the frames of one camera in time order; says when the picture has stopped changing. Memory is bounded by
    the frames of one look back second plus one."""

    def __init__(self, limit_per_s: float) -> None:
        if not limit_per_s > 0:
            raise ValueError("settling limit must be positive")
        self._limit = limit_per_s
        self._history: deque[tuple[int, Means]] = deque()

    def settled(self, t_ns: int, means: Means) -> bool:
        if self._history and t_ns <= self._history[-1][0]:
            raise ValueError("frames must arrive in time order")
        self._history.append((t_ns, means))
        cutoff = t_ns - LOOKBACK_NS
        while len(self._history) > 2 and self._history[1][0] <= cutoff:
            self._history.popleft()
        then, earlier = self._history[0]
        if then > cutoff:
            return False  # not one second of picture yet
        seconds = (t_ns - then) / 1e9
        change = max(abs(now - before) for now, before in zip(means, earlier, strict=True))
        return change / seconds <= self._limit
