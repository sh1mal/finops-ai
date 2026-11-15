CREATE TABLE IF NOT EXISTS purchase_order_lines (
    po_number text NOT NULL REFERENCES purchase_orders(number),
    line_number integer NOT NULL CHECK (line_number > 0),
    description text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit text NOT NULL CHECK (unit IN ('each', 'box', 'hour', 'kg')),
    unit_price_cents bigint NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY (po_number, line_number)
);

ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS variant_family text;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS variant text;
