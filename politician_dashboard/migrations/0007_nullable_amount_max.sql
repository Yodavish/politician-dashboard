-- The Senate eFD reports its unbounded top disclosure tier as "Over
-- $50,000,000", which states a lower bound and no upper bound. Storing that
-- as a concrete amount_max would claim a precision the source withholds, so
-- amount_max becomes nullable and holds NULL for open-ended amounts.
--
-- amount_min stays NOT NULL: every disclosed amount has a lower bound.
--
-- This is additive. Existing rows keep their concrete amount_max values, and
-- the column is only more permissive than before.

ALTER TABLE transactions ALTER COLUMN amount_max DROP NOT NULL;

COMMENT ON COLUMN transactions.amount_max IS
    'Upper bound of the disclosed amount, or NULL when the source states only a lower bound (eFD "Over $X" tier).';
