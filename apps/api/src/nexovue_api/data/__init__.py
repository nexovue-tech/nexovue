"""Every store the control plane touches.

Database access lives here rather than inside the domain packages: twelve
domains holding their own query builders means twelve places to forget the
tenant filter.
"""
