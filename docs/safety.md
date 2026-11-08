# Safety and recovery

Autonomous policy application is not implemented. The restricted restore backend can restore runtime CPUWeight from a recorded original for the same registered unit invocation.

The implementation must restrict actions to managed workloads and allowlisted operations, validate targets and bounds, record previous settings, and support expiration and explicit restoration. Recovery must reconcile persisted state after a controller failure.

Never pass language-model output directly to a shell or privileged resource interface. See the [README safety requirements](../README.md#architecture-and-safety).

Next: define executor permissions, rejection cases, and recovery behavior before enabling writes.

Recovery behavior and limitations are documented in [restore usage](restore.md). Unregister is blocked while CPU recovery is pending. Future policy writers must serialize capture, apply, and restore rather than racing independent workers.
