"""Business-logic services used by the API layer.

Keeping authentication (and future job/category logic) out of the route
handlers makes each handler a thin orchestration layer and keeps the
testable logic in plain async functions that don't require a running
ASGI app.
"""
