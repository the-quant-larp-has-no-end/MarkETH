"""The API contract: every request and response body of SPEC §10 and Appendix A.2.

One module per route module of `app/api/routes`. Routes validate input with these models
and services return them, so they double as the data-transfer objects of the backend
(backend/CLAUDE.md, "Layers"). The package imports only `app.core`.

Conventions (Appendix A.2):

* Requests are named `*Request` (bodies) or `*Query` (query strings), responses
  `*Response`; a model nested inside a response is a plain noun.
* Credits are `*_cr` fields and shares are `shares` fields: `Decimal` in Python, decimal
  strings in JSON. The types are in `app.schemas.common`.
* Probabilities are JSON numbers in [0, 1], already rounded for display.
* Instants are timezone-aware and travel as ISO 8601 strings; calendar dates as
  `YYYY-MM-DD`.
* A response field is never optional: what can be missing is nullable and always sent.
"""
