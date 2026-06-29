"""Domain/service layer: all config, scoring, restriction, and game-state access.

Cogs call these; a future dashboard API reuses the same services. Keep DB access
here, not in interaction callbacks.
"""
