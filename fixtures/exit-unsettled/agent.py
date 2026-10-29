"""Test fixture: on its first prompt, log one line, then exit 1 without settling or failing — the node must name it."""
import os
import sys

import jaah_agent


class ExitUnsettled(jaah_agent.Agent):
    name = "exit-unsettled"
    version = "2.0.0"

    async def prompt(self, turn: jaah_agent.Turn) -> None:
        print("exit-unsettled fixture: exiting 1 without settling", file=sys.stderr, flush=True)
        os._exit(1)  # no turn end, no settle: the process is simply gone


if __name__ == "__main__":
    jaah_agent.run(ExitUnsettled())
