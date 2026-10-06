"""Self-evolution cycle (F3): Failure Memory, config edits, the candidate archive, replay and
the Improvement Planner (GEPA prompt stage, topology stage).

Planner-side modules (everything here except ``promotion``) never import
``womm.eval.holdout``; ``tests/evolve/test_planner_boundary.py`` enforces it.
"""
