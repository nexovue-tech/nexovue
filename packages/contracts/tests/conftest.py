"""Put the committed generated bindings on the import path.

`gen/python` is generated output rather than a distributable package, so it is not
installed — the tests import it from the tree the way every service will.
"""

import sys
from pathlib import Path

GENERATED_PYTHON_ROOT = Path(__file__).resolve().parent.parent / "gen" / "python"

if str(GENERATED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERATED_PYTHON_ROOT))
