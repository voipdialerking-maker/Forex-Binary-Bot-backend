-- SUPABASE SETUP SQL FOR BINARY TRADING SIGNALS
-- Copy and paste this script into the SQL Editor in your Supabase Dashboard

-- 1. Create the signals table
CREATE TABLE IF NOT EXISTS public.signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pair TEXT NOT NULL,
    type TEXT NOT NULL,                          -- 'CALL' or 'PUT'
    entry_price NUMERIC NOT NULL,
    expiry_price NUMERIC,
    status TEXT NOT NULL DEFAULT 'ACTIVE',       -- 'ACTIVE', 'WON', 'LOST', 'TIE'
    rsi_value NUMERIC,
    stochastic_k NUMERIC,
    volume_value NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expiry_time TIMESTAMPTZ NOT NULL
);

-- 2. Create index on created_at for faster queries and history loading
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON public.signals (created_at DESC);

-- 3. Disable Row Level Security (RLS) for testing or simple setup
-- Note: This allows your python backend and React frontend to read/write using the Anon key.
ALTER TABLE public.signals DISABLE ROW LEVEL SECURITY;

-- ALTERNATIVE: If you want to KEEP RLS enabled, uncomment the lines below to create public policies:
/*
ALTER TABLE public.signals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read access" 
ON public.signals FOR SELECT 
USING (true);

CREATE POLICY "Allow public write access" 
ON public.signals FOR INSERT 
WITH CHECK (true);

CREATE POLICY "Allow public update access" 
ON public.signals FOR UPDATE 
USING (true);
*/

-- 4. ENABLE REALTIME
-- This is critical! Without this, the React frontend cannot subscribe to live signal popups.
-- If the publication does not exist, it will be created, otherwise we add the table to it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'
    ) THEN
        CREATE PUBLICATION supabase_realtime;
    END IF;
    
    -- Add signals table to the publication
    ALTER PUBLICATION supabase_realtime ADD TABLE public.signals;
EXCEPTION
    WHEN duplicate_object THEN
        -- Table is already in the publication, ignore error
        NULL;
END $$;
