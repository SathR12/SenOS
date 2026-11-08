# Architecture

The initial architecture is described in the [README](../README.md#architecture-and-safety).

The CLI calls application services; it must not contain policy logic. Agents produce proposals, deterministic checks validate them, and the restricted executor applies approved operations. Shared contracts belong in `src/senos/contracts/`; their field meanings and validation rules are documented in [Shared data contracts](contracts.md).

Next: define component service interfaces around the shared contracts before implementing agents independently.
