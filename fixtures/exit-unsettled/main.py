"""Test fixture: log one line, then exit 1 without settling or failing — the supervisor must name it."""
import sys

import jaah_agent

jaah_agent.connect().log("exit-unsettled fixture: exiting 1 without settling", level="error")
sys.exit(1)
