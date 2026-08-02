"""Source parsers: claude_code, codex, cursor.

Each parser exposes a ``parse_*_session`` function returning
``(session: dict, events: list[dict])`` per the normalized shapes in
``docs/contracts/session.json`` and ``docs/contracts/event.json``.
"""
