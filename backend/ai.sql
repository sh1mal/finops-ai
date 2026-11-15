ALTER TABLE invoices ADD COLUMN IF NOT EXISTS ai_extraction jsonb;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS ai_investigation jsonb;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS description text;
