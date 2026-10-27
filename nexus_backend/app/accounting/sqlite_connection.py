"""Local runtime transaction identity, independent of user SQL and diagnostics."""

import sqlite3
from uuid import uuid4


class AccountingSQLiteConnection(sqlite3.Connection):
    """Keep review triggers aware of actual transaction boundaries.

    A trace runs on execution, including cached/implicit BEGIN and SAVEPOINT,
    rather than just statement preparation. Outside a transaction the UDF is
    empty. Read-only diagnostic tracing cannot replace the internal callback.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._review_transaction = ""
        self._external_trace = None
        super().set_trace_callback(self._trace_statement)
        self.create_function("accounting_transaction_id", 0, self._transaction_id)

    def _trace_statement(self, statement):
        # Before BEGIN executes, autocommit is still on. Any transaction entered
        # after COMMIT/ROLLBACK therefore gets a fresh, unguessable identifier.
        # Nested statements and savepoints inside a transaction retain it.
        if not self.in_transaction:
            self._review_transaction = uuid4().hex
        if self._external_trace is not None:
            self._external_trace(statement)

    def _transaction_id(self):
        return self._review_transaction if self.in_transaction else ""

    def set_trace_callback(self, callback):
        self._external_trace = callback
        super().set_trace_callback(self._trace_statement)
