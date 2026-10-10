"""Adapters for everything that is not Postgres: Redis and ScyllaDB (SPEC §9).

None of them decides a balance, a price or whether a trade is allowed. Every class in
this package states what it does when its backend is unreachable, and each of those
answers lets the caller carry on.
"""
