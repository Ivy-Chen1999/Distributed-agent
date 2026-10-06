"""Self-evolution cycle (F3): Failure Memory, config edits, the candidate archive and replay.

Planner-side modules (everything here except a future ``promotion`` module) never import
``womm.eval.holdout``; ``tests/evolve/test_planner_boundary.py`` enforces it.
"""
