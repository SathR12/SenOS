# Safety and recovery

The scaffold does not apply resource policies.

The implementation must restrict actions to managed workloads and allowlisted operations, validate targets and bounds, record previous settings, and support expiration and explicit restoration. Recovery must reconcile persisted state after a controller failure.

Never pass language-model output directly to a shell or privileged resource interface. See the [README safety requirements](../README.md#architecture-and-safety).

Next: define executor permissions, rejection cases, and recovery behavior before enabling writes.
