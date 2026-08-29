"""Door 4b: pull metrics out of a customer's cloud account and databases.

A poller is the opposite of a probe. A probe measures something from outside; a
poller asks a system that is already measuring itself. Both end up as OTLP
metrics inside the one envelope, which is why neither needs a table or a worker
of its own.

The frozen examples of this door are
`packages/contracts/fixtures/door4b-cloudwatch.bin` and `door4b-dbstats.bin`.
Where this package and those disagree, they are right.
"""
