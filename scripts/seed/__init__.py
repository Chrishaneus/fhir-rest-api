"""Programmatic FHIR R5 seeder built on `fhir.resources` Pydantic models.

The runtime persists resources directly through `app.store.FHIRStore`, so no
HTTP server has to be running and no JSON files have to be parsed. The Pydantic
factories double as living, type-checked documentation of which resource
shapes we expect to see in this project's data.

See `scripts.seed.runner` for the registry, topological sort, and CLI; the
individual factories live under `scripts.seed.factories`.
"""
