# 0076 — Door 4b holds no customer credential it can avoid holding

- **Status:** Accepted
- **Date:** 2026-08-29

## Context

Door 4b polls systems that belong to the customer: their AWS account, their
Postgres. Something has to authenticate. The Phase 1 plan row for P1-37 says
"credentials encrypted at rest", and `apps/api/migrations/versions/0001_control_plane_schema.py`
already created the column that phrasing implies — `integrations.config_encrypted`,
a `LargeBinary`.

"Encrypted at rest" is the wrong goal to aim at, because it describes the
storage and not the liability. A stored AWS access key is a standing grant: it
works until someone rotates it, it works from anywhere, and encrypting the
column does nothing about the fact that our process must decrypt it to use it.
Every customer who connects an account would be trusting us with a key that
outlives the relationship, and a breach of one row would be a breach of that
customer's AWS account rather than of a database column.

The relevant question is not *how do we store the secret* but *do we need to
hold one at all*.

For AWS, we do not. AWS's own Well-Architected guidance for exactly this
situation — SEC03-BP09, *Share resources securely with a third party* — states
the desired outcome as: "You avoid using long-term AWS Identity and Access
Management (IAM) credentials like access keys and secret keys, as they pose a
security risk if misused. Instead, you use IAM roles and temporary credentials";
and "When granting third-party access, you use a universally unique identifier
(UUID) as the external ID in the IAM trust policy." It lists "Using long-term
IAM credentials and access keys" as a common anti-pattern. (Fetched
2026-08-29 from
<https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_permissions_share_securely_third_party.html>.)

The ExternalId is not decoration. Without it, a role's trust policy that names
our account will accept an AssumeRole call from us on behalf of *anyone*, so a
person who learns another customer's role ARN can ask us to poll that account
and we will faithfully do so. AWS names this the confused deputy problem, and
says of the external ID: "The primary function of the external ID is to address
and prevent the confused deputy problem" (fetched 2026-08-29 from
<https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html> and
<https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html>).
The same guidance tells the third party — us — that the external ID must be
generated per integration, must not be reusable, and must not be editable by
the customer after setup.

For RDS Postgres we also do not need a password. RDS supports IAM database
authentication for PostgreSQL, where a short-lived token replaces the password:
"Each token has a lifetime of 15 minutes. You don't need to store user
credentials in the database, because authentication is managed externally using
IAM", and "Network traffic to and from the database is encrypted using Secure
Socket Layer (SSL) or Transport Layer Security (TLS)" (fetched 2026-08-29 from
<https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.html>).
`boto3` mints the token locally with SigV4 — verified against the installed
boto3 1.43.83, whose `RDS.Client.generate_db_auth_token(DBHostname, Port,
DBUsername, Region=None)` returns a presigned string with `X-Amz-Expires=900`
and an `X-Amz-Security-Token` from the assumed role, with no API call.

Self-hosted Postgres has no equivalent. That case genuinely requires storing a
secret, and storing one properly requires a key-management decision this project
has not taken.

## Decision

**Door 4b stores no customer credential. It stores two public values and mints a
short-lived credential per poll.**

- **AWS / CloudWatch.** The customer creates a role in their own account whose
  trust policy names our account as principal and requires a random `ExternalId`
  we generate per integration. We store the role ARN and that ExternalId, both
  of which are public: neither grants anything on its own, and access is
  conferred by the trust policy in the customer's account, which they can revoke
  without telling us. At poll time `assume_customer_role` calls STS AssumeRole
  with the ExternalId and `DurationSeconds=900` — the shortest AWS allows,
  because a poll takes seconds and credentials that outlive it are a liability
  with no benefit. `RoleGrant.__post_init__` refuses to construct a grant with
  an empty ExternalId, so no code path can reach AssumeRole without one.

- **The AWS account id is read off the role ARN**, never stored separately. It
  becomes `cloud.account.id` on every reading, and a second stored copy could
  disagree with the ARN actually being assumed.

- **Postgres on RDS.** The same role is assumed, then `generate_db_auth_token`
  signs a 15-minute token used as the connection password, over TLS
  (`ssl="require"`, which RDS requires for IAM auth anyway). Nothing is stored
  and nothing is written to disk.

- **Self-hosted Postgres is deliberately not implemented.** `DatabaseTarget`
  refuses `auth="password"` with a message naming this ADR rather than falling
  back to something weaker.

- **`integrations.config_encrypted` holds plain UTF-8 JSON**, because nothing in
  it needs encrypting. The column name predates this decision and is now
  misleading; renaming it is listed under Consequences.

## Alternatives considered

**Store an IAM access key and secret, encrypted at rest.** Rejected. It is the
anti-pattern AWS names, it is a standing grant that survives the customer
relationship, and the encryption protects the column rather than the account —
the poller must decrypt to use it, so anything that can read our process can
read the key. Revocation would also depend on us acting rather than on the
customer, which is backwards.

**A single encryption key in an environment variable, with the credential
encrypted under it.** Rejected. One key for every tenant means one compromise
decrypts every customer's credentials, there is no rotation story that does not
require re-encrypting every row, and the key ends up in the same deployment
config as everything else — so the blast radius of a leaked env file is every
integration in the system. It is envelope encryption's problem restated without
envelope encryption's protections.

**Assume the role without an ExternalId.** Rejected on the confused-deputy
grounds above. It would also be the weaker offer: AWS's own advice to customers
is to test whether the vendor's role can be assumed without the external ID, and
to withhold the ARN if it can.

**Discover RDS instances automatically instead of naming them.** Rejected for
now. CloudWatch bills per metric read, and a poller that expands to every
instance in an account bills the customer for metrics they never asked us to
watch. `db_instance_identifiers` is explicit.

**Implement envelope encryption now so self-hosted Postgres works.** Rejected as
out of scope for this step. Doing it properly means a KMS or equivalent, a data
key per integration, a rotation procedure and an audit trail — a decision of its
own, not a detail of a poller. Doing it badly would be worse than the feature
being absent, because it would look like protection.

## Consequences

**What this buys.** A breach of the `integrations` table yields role ARNs and
ExternalIds and no access to anything. Revocation is the customer deleting a
role, and takes effect on our next poll without our involvement. Every poll is
attributable in the customer's own CloudTrail under a session name that carries
the integration id (`session_name_for`).

**What this costs.**

- **Onboarding is harder.** A customer can no longer paste a key into a form:
  they must create a role, paste our account id and their generated ExternalId
  into a trust policy, and paste the ARN back. That is several minutes and a
  place to make a mistake, against roughly thirty seconds for a key. A
  CloudFormation template would remove most of it and does not exist yet.

- **Self-hosted Postgres is not supported at all.** Customers not on RDS get an
  explicit refusal. This is a real product gap and is deferred, not solved.

- **A poll costs one extra AWS API call.** AssumeRole runs on every poll; the
  credentials are not cached across tasks, because Celery task state is not a
  place to keep credentials. At a 60-second interval that is one STS call per
  integration per minute, well inside STS's limits but not free.

- **A misconfigured trust policy fails at poll time, not at setup time.** There
  is no "test connection" path yet, so the first sign of a wrong ExternalId is a
  failing task. Adding one is API work.

- **The column name is now wrong.** `integrations.config_encrypted` holds
  plaintext JSON. It should be renamed `config` in the next migration that
  touches the control-plane schema; leaving it means a future reader assumes a
  protection that is not there — which is more dangerous than the honest name.

**What was verified, and what was not.** The AssumeRole and RDS-token paths are
tested offline with `botocore.stub.Stubber`, which validates request parameters
against AWS's own service model; the ExternalId assertion was confirmed to fail
when the parameter is dropped. **No call was made to real AWS**, so the
end-to-end claim — that a customer-created trust policy accepts our AssumeRole
and that the resulting token opens an RDS connection — is untested and rests on
the documentation cited above. The `pg_stat_statements` queries have likewise
not been run against a live Postgres.
