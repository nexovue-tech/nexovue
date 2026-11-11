"""F3: an organization's AI settings (`/settings/ai`; spec FR23 to FR32; design *API*).

The API never calls a provider (A3): a connection test is a job on the `insight` queue, and
this package imports only `nexovue_platform.ai`'s pure parts, never the transport (AC35).
"""
