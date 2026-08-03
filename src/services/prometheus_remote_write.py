"""Back-compat alias for ``src.services.metrics.prometheus_remote_write``.

The module moved into the ``metrics`` sub-package. This shim keeps the old
import path working. The final line rebinds this module name to the canonical
module object, so both paths resolve to ONE module with ONE set of globals.

That last part is the whole point. A previous meta-path-finder approach left
the two paths pointing at two separately-executed copies of the same file, so
module-level state diverged: ``init_prometheus_remote_write()`` set the writer
on one copy while ``get_prometheus_writer()`` read ``None`` from the other and
metrics were silently dropped. Import machinery re-reads ``sys.modules`` after
executing a module, which is what makes this idiom reliable where the finder
was not.

Prefer the canonical path in new code.
"""

import sys

from src.services.metrics.prometheus_remote_write import *  # noqa: F401,F403
from src.services.metrics.prometheus_remote_write import __dict__ as _canonical_dict  # noqa: F401

sys.modules[__name__] = sys.modules["src.services.metrics.prometheus_remote_write"]
