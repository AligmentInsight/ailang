"""Aligment Insight plugin bundle: a deterministic word counter.

Drop any file like this into `plugins/` (or `agents/`) and its kinds are
discovered automatically - no `use` needed, and `ailang build` records the
file so a compiled image can restore the kind.
"""


class CountWords:
    def __init__(self, fields, config):
        self.name = "count-words"
        self.last_insight = None

    def invoke(self, task, source="human"):
        self.last_insight = str(len(str(task).split()))
        return self.last_insight


def register(api):
    api.kind("count-words", CountWords)
