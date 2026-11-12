"""What the API and the scheduler must agree on about saved dashboards (F20, FR51)."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

# A deleted dashboard can be restored for this long, then the expiry sweep removes it and
# its versions. The API's restore refuses past it and the sweep deletes past it, so a
# dashboard is never both restorable and gone.
RESTORE_FOR: Final = timedelta(days=30)
