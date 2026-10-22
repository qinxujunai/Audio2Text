"""Per-platform source logic.

New platform code lives here, one module per platform, as pure functions where possible
(URL builders, payload parsers) so they can be tested with recorded fixtures. ``app.extractors``
keeps the orchestration (timeouts, downloads, ExtractionOutcome) and is being moved here one
platform at a time; see AGENTS.md.
"""
