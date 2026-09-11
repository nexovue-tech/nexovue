"""Ingest credentials of both kinds: secret server keys and public site keys.

The kind is the table — `api_keys` for the secret kind, `site_keys`
for the public one — so no lookup here can return the wrong one.
"""
