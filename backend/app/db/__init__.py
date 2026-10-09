"""Database plumbing: the declarative `Base` and the `Database` engine holder (SPEC §7)."""

from app.db.base import Base
from app.db.database import Database

__all__ = ["Base", "Database"]
