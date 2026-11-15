"""Property tests check grouping, sampling and parsing rules, not speed, so example deadlines are off.

On a loaded machine (mutation runs, the hub suite in parallel) Hypothesis's default 200 ms deadline
failed a correct partition property; timing budgets are T-E18-18's job, on the reference machine.
"""

from hypothesis import settings

settings.register_profile("e18", deadline=None)
settings.load_profile("e18")
