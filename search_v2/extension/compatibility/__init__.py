"""
Deliberately minimal. Only for shims that are genuinely unavoidable during
migration (e.g. a response-shape adapter needed while both V1 and V2 code
paths must coexist mid-migration). Per the migration brief: "do not
introduce additional compatibility layers unless absolutely unavoidable" —
expect this package to stay near-empty. Anything added here should note,
in its own docstring, exactly why it couldn't be a native V2 implementation
instead.
"""
