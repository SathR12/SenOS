"""Recovery errors safe to show through the CLI."""


class RestoreError(Exception):
    """A recorded policy could not be restored or verified."""
