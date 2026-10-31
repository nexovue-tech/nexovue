"""Which Postgres tables hold a tenant's data, and how each one is removed.

The counterpart of `purge/tables.py`, and here for the same reason: "what a
deletion covers" has to be reviewable in the source rather than inferred from
whatever `DELETE` statements happen to exist. `tests/test_deletion_live.py`
holds this list against the live catalog, so a table added next month fails the
deletion test the day it is created rather than leaving rows behind the first
time somebody is erased.

**Why not one `DELETE FROM organizations`.** Every tenant-scoped table does
cascade from it — that was checked, not assumed — so a single statement would
in fact clear them all today. It is not used, for two reasons. The organization
row is kept (see `TOMBSTONED_TABLE`), so nothing would cascade from it anyway.
And a cascade is invisible: it covers a new table only if whoever wrote that
table remembered `ondelete="CASCADE"`, and a table that used `SET NULL`, or
carried `org_id` with no foreign key at all, would be silently left behind. What
is enumerated here can be tested; what a cascade does cannot.
"""

from __future__ import annotations

from typing import Final

# The column a tenant's rows are found by, everywhere it appears.
TENANT_COLUMN: Final = "org_id"

# Deleted by a statement of their own, in this order — children before parents,
# so no foreign key is ever left dangling mid-transaction. The two billing
# tables are here rather than under a cascade because they hang off the
# organization alone: nothing else erases them once the organization row is the
# thing being kept.
DELETED_DIRECTLY: Final[tuple[str, ...]] = (
    "billing_webhook_deliveries",
    "billing_subscriptions",
    # F36. The organization's read tokens; their daily counters cascade with them.
    "api_tokens",
    # F31. The payment that bought the organization, like the billing rows above;
    # a comped plan; and the owner's consent to support, which names a user.
    "website_payments",
    "plan_grants",
    "support_access_grants",
    "audit_log",
    # Alert channels belong to the organization, not a project (F21, D4). Before
    # `users`, whom a mute names, and before `projects`, whose rules send to them.
    "notification_channels",
    # ADR-020. Queued work for this organization, and what a Run now or a send
    # test found; neither hangs off a person or a project.
    "jobs",
    "job_results",
    "users",
    "projects",
)

# Removed because the row that owns them is, with the parent that takes them.
# Each pair is verified against the live catalog: the child's foreign key to
# that parent must be `ON DELETE CASCADE` and its column NOT NULL, or a row with
# a null parent would survive the deletion.
REMOVED_BY_CASCADE: Final[dict[str, str]] = {
    "api_keys": "projects",
    "site_keys": "projects",
    "checks": "projects",
    "check_states": "projects",
    "check_state_changes": "projects",
    "check_secrets": "projects",
    "check_variables": "projects",
    # Its `check_id` is SET NULL so a deleted check's files can be swept; the
    # project is the parent that takes the row with it.
    "check_files": "projects",
    "maintenance_windows": "projects",
    "maintenance_window_checks": "projects",
    "integrations": "projects",
    "deploy_events": "projects",
    "project_webhook_secrets": "projects",
    "project_first_traces": "projects",
    "source_maps": "projects",
    "usage_daily": "projects",
    "usage_daily_services": "projects",
    # F21. A rule's channels and evaluations go with the rule; incidents with the
    # project; the delivery log with its channel. Notifications go with the project:
    # their subject is an incident, a finding or the project itself (F6, D24), so
    # `incident_id` may be null and cannot be the parent.
    "alert_rules": "projects",
    "alert_rule_channels": "alert_rules",
    "alert_evaluations": "alert_rules",
    "incidents": "projects",
    "notifications": "projects",
    "notification_attempts": "notification_channels",
    # F6. Findings and everything detection keeps per service go with the project;
    # a finding's links to incidents go with the finding.
    "findings": "projects",
    "finding_incidents": "findings",
    "detection_state": "projects",
    "detection_project_runs": "projects",
    "detection_baselines": "projects",
    "baseline_shifts": "projects",
    "service_detection_settings": "projects",
    "service_last_seen": "projects",
    "error_groups_seen": "projects",
    "error_group_watermarks": "projects",
    # The one child of `users` rather than of `projects`. It carries no `org_id`
    # on purpose — `models.UserRecoveryCode` argues why — so it is reached here
    # through the person it belongs to, and `users` is deleted directly above.
    "user_recovery_codes": "users",
    # F36. Keyed by token, carrying no `org_id`: the token it names carries one,
    # and the counters die with it.
    "api_token_usage_daily": "api_tokens",
    # ADR-020: what Redis held, each keyed by the person it belongs to. Sessions
    # and support tickets carry `org_id` too, but the person is the parent.
    "sessions": "users",
    "view_as_tickets": "users",
    "one_time_tokens": "users",
    "totp_enrolments": "users",
    "totp_challenges": "users",
    "totp_spent_codes": "users",
}

# The organization row itself, which a deletion scrubs and keeps rather than
# removes. See `organizations/service.py` for why.
TOMBSTONED_TABLE: Final = "organizations"

# Everything else, with the reason it is left alone. Data rather than prose so
# the live test can assert that every table in the database is either erased,
# cascaded, tombstoned or excluded on purpose — an unclassified table is the
# failure this module exists to prevent.
EXCLUDED_TABLES: Final[dict[str, str]] = {
    "plans": "the tiers themselves; reference data every tenant shares and none owns",
    "plan_prices": "what a tier costs, from a date. Shared reference data, like `plans`",
    "usage_ingest_offsets": (
        "keyed by topic and partition, not by tenant — the Postgres twin of the "
        "ClickHouse watermark tables. Losing a row makes the meter re-count "
        "ingestion it already counted: a silent, permanent over-count"
    ),
    "alembic_version": "Alembic's own bookkeeping; holds no customer data",
    "alert_evaluation_status": (
        "when each alert sweep last finished — one row per sweep, across every tenant"
    ),
    "rate_limit_windows": (
        "the auth tier's counters, keyed by a fingerprint of an address or IP and gone "
        "within the hour; nothing in a row names a tenant"
    ),
    "intake_refusals": (
        "the receiver's refusals for lack of a store, per minute and receiver (F6, C5). A "
        "broker failure refuses every tenant alike, so nothing in a row names one; pruned "
        "by the expiry sweep"
    ),
    "detection_events": (
        "what detection did on its own that nexovue's operator must see, such as a held "
        "minute judged after 30 minutes (F6). It describes the pipeline, not a tenant; "
        "pruned by the expiry sweep"
    ),
}
