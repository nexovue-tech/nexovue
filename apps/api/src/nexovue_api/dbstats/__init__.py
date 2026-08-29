"""What the door 4b poller read out of a customer's Postgres.

Named `dbstats` rather than `database`: this package is one signal, and a module
called `database` inside a service that owns two of them reads as the service's
own storage layer.
"""
