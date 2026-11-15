"""Kept so older imports keep working. The logic now lives in lenses/ (one file per blind
spot) and report.py (plan, merge, coverage). Remove this file once nothing imports it."""
from lenses.base import *  # noqa: F401,F403
from lenses.base import _STAKE, AFFILIATE_LINK  # noqa: F401
from lenses.memory import MEMORY_SYSTEM, PHRASINGS, cluster_answers  # noqa: F401
from lenses.money import NORMAL_SYSTEM, PICK_SYSTEM  # noqa: F401
from lenses.time import NEW_SYSTEM  # noqa: F401
from report import PLAN_SYSTEM, run  # noqa: F401
