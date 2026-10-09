"""The HTTP layer: routes, dependencies, error handlers and middleware (SPEC §10).

A route parses its input, calls one service method and returns the result. No SQL and
no business rule lives here (backend/CLAUDE.md, "Layers").
"""
