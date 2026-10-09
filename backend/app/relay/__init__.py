"""The outbox relay: its own process, `python -m app.relay` (SPEC §9, Appendix A.10)."""

from app.relay.outbox_relay import OutboxRelay

__all__ = ["OutboxRelay"]
