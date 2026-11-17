CREATE TABLE IF NOT EXISTS fee_receivable_items (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    batch_row_id TEXT NOT NULL,
    period TEXT NOT NULL,
    driver_id TEXT,
    driver_name TEXT NOT NULL,
    vehicle_name TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','settled')),
    created_at TEXT NOT NULL,
    UNIQUE(batch_row_id)
);
CREATE INDEX IF NOT EXISTS fee_receivable_items_book_period
    ON fee_receivable_items(book_id, period, status);
