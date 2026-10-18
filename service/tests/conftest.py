"""Make repository packages importable for absolute-path pytest invocations.

Container validation invokes pytest with ``/app/service/tests`` while the
process working directory may be elsewhere. Python then places
``/app/service/tests`` on ``sys.path``, not the repository root. This test-only
bootstrap restores the same import root used by the application without
changing runtime module loading.
"""

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
