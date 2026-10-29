"""Which kind of install this is: nexovue's hosted service, or a customer's own.

One setting, `NEXOVUE_DEPLOYMENT`, read by the API, the scheduler and the receiver
(docs/changes/ready-to-deploy, Step 2). A self-hosted install has no billing, no payment
processor, no plan allowance and no operator console: the operator's disk is theirs, and
nobody at nexovue administers it.

**Self-hosted is the default.** The images are public, so whoever runs them without saying
otherwise is self-hosting; only the hosted service sets `hosted`, and says so explicitly.
"""

from __future__ import annotations

from typing import Final, Literal

Deployment = Literal["hosted", "self-hosted"]

DEPLOYMENT_VARIABLE: Final = "NEXOVUE_DEPLOYMENT"
HOSTED: Final[Deployment] = "hosted"
SELF_HOSTED: Final[Deployment] = "self-hosted"
DEFAULT_DEPLOYMENT: Final[Deployment] = SELF_HOSTED
