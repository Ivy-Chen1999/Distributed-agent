"""Self-evolution cycle (F3): Failure Memory, config edits, the candidate archive, replay, the
Improvement Planner (GEPA prompt stage, topology stage), the R37 diff check and the promotion
gate.

Planner-side modules (everything here except ``promotion``) never import
``womm.eval.holdout``, and none of them except ``diff_regression`` reaches the R37 reference
answers; ``tests/evolve/test_planner_boundary.py`` enforces both.
"""
