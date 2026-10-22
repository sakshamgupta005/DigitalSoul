import copy
import math
from dataclasses import dataclass, field

from .errors import GhostError


def typed(value, kind):
    valid = {
        "string": lambda: type(value) is str,
        "string[]": lambda: type(value) is list and all(type(v) is str for v in value),
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
        "boolean": lambda: type(value) is bool,
    }
    if kind not in valid or not valid[kind]():
        raise GhostError("VALIDATION_ERROR", "Value does not match declared type", 422)
    return copy.deepcopy(value)


@dataclass
class ExecutionContext:
    values: dict = field(default_factory=dict)
    loop_path: list = field(default_factory=list)

    def child(self, step_id, index, item, name):
        return ExecutionContext(
            {**copy.deepcopy(self.values), name: item},
            self.loop_path + [{"step_id": step_id, "index": index, "item": item}],
        )
