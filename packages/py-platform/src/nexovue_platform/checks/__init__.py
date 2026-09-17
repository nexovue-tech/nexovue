"""What a synthetic check is, stated once for the API that stores it and the
scheduler that runs it.

The API validates a definition against these models before it is saved; the
scheduler parses the same models out of the task it is handed. Two copies of the
shape would let a check the API accepted be one the prober cannot read.
"""
