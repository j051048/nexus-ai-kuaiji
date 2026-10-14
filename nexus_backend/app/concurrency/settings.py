import os
from dataclasses import dataclass


def bounded_integer(name, default, maximum=8, environ=None):
    env = os.environ if environ is None else environ
    try:
        value = int(env.get(name, str(default)))
        if not 1 <= value <= maximum:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须为 1–{maximum} 的整数") from None
    return value


@dataclass(frozen=True)
class SchedulingSettings:
    # Thread concurrency is local; the database policy is the fleet-wide ceiling.
    requested_global_limit: int = 2

    @classmethod
    def from_env(cls, environ=None):
        return cls(bounded_integer("ACCOUNTING_PROCESSING_GLOBAL_LIMIT", 2, environ=environ))
