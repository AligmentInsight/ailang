"""AI-lang plugin: kinds "shout" and "shout-loud" — deterministic agents.

Every value in AI-lang is an agent; these just shout. They exist to prove
the plugin contract (see PLUGINS.md) with zero network access, including
a hyphenated kind name.
"""


class ShoutAgent:
    def __init__(self, fields, config):
        self.name = fields.get("model", "shout")
        self.last_insight = None

    def invoke(self, task, source="human"):
        self.last_insight = str(task).upper()
        return self.last_insight


class ShoutLoudAgent(ShoutAgent):
    def invoke(self, task, source="human"):
        return super().invoke(task, source) + "!!!"


def register(api):
    api.kind("shout", ShoutAgent)
    api.kind("shout-loud", ShoutLoudAgent)
