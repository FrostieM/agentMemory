"""Theory domain helpers — promotion bridge and related logic.

Sits between ``ingestion/theory_writer.py`` (which writes new theory rows
and adds evidence) and the ``decisions`` package (which consumes the
candidate proposals). Keeps the trust-gate invariant: validated theories
surface as candidates, never as automatic decisions.
"""
