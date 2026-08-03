"""Back-compat alias for ``src.services.providers.deepinfra_catalog``.

The module moved into the ``providers`` sub-package. The final line rebinds this
name to the canonical module object so both import paths resolve to ONE module
with ONE set of globals — see docs/FIX_module_aliasing.md.

Prefer the canonical path in new code.
"""

import sys

from src.services.providers.deepinfra_catalog import *  # noqa: F401,F403

sys.modules[__name__] = sys.modules["src.services.providers.deepinfra_catalog"]
