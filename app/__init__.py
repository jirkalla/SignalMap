"""SignalMap application package.

`__version__` is the single source of truth for the application version — `FastAPI(version=...)`,
the worker heartbeat, and the page footer all import it rather than keeping their own copy
(docs/TASKS_VERSIONING.md design decisions 1 and 2). It lives in code, not git: the deploy ships a
`git archive`, so /opt/signalmap is not a git checkout and `git describe` cannot work there.
Bump rules: docs/DEPLOYMENT.md. History: CHANGELOG.md.
"""

__version__ = "1.1.0"
