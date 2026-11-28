"""Releases (F28): `/releases`, the record of each version a project's services ran,
from traffic and, from Step 2, from deploy reports, with a comparison by version.

The list and one release read Postgres alone; the comparison reads the version rollup
through the tenant repository. The arithmetic is `nexovue_platform.releases`, shared with
the scheduler (D26).
"""
