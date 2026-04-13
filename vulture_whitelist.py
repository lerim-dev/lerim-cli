"""Vulture whitelist — false positives from framework/protocol methods.

Vulture cannot trace dynamic dispatch (getattr), protocol-style dispatch
(`forward`, HTTP `do_GET`), or sqlite3 attributes (`row_factory`). List them here
so CI passes cleanly.
"""

# Module protocol method — invoked by runtime dispatch
forward  # noqa

# HTTP handler methods — called by BaseHTTPRequestHandler dispatch
do_GET  # noqa
do_POST  # noqa
do_PUT  # noqa
do_PATCH  # noqa
do_DELETE  # noqa
log_message  # noqa
server_version  # noqa

# sqlite3 cursor attribute — set, not called
row_factory  # noqa

# loguru config attributes
_rotation  # noqa
_retention  # noqa
handlers  # noqa

# Pydantic model fields — populated at validation time
artifacts  # noqa

# Base class — subclassed by platform adapters
Adapter  # noqa

# Lazy import pattern
__getattr__  # noqa

# Used by 30+ test cases in test_transcript.py
format_transcript  # noqa

# Called via getattr() dynamic dispatch in CLI (_dead_letter_action)
retry_project_jobs  # noqa
skip_project_jobs  # noqa
