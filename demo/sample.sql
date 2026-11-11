\set ON_ERROR_STOP on
CREATE TABLE public.customers (
    id integer PRIMARY KEY,
    name text NOT NULL UNIQUE,
    city text NOT NULL
);
CREATE TABLE public.orders (
    id integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES public.customers(id),
    total numeric(12, 2) NOT NULL CHECK (total >= 0),
    status text NOT NULL CHECK (status IN ('paid', 'pending', 'cancelled')),
    ordered_at date NOT NULL
);
CREATE INDEX orders_customer_id_idx ON public.orders(customer_id);
INSERT INTO public.customers VALUES
    (1, 'Ada', 'London'), (2, 'Grace', 'New York'), (3, 'Linus', 'Helsinki');
INSERT INTO public.orders VALUES
    (1, 1, 120.00, 'paid', '2026-01-10'),
    (2, 1, 80.00, 'pending', '2026-01-12'),
    (3, 2, 250.00, 'paid', '2026-01-15');
CREATE VIEW public.order_summary AS
    SELECT c.name, COUNT(o.id) AS order_count
    FROM public.customers c LEFT JOIN public.orders o ON o.customer_id = c.id
    GROUP BY c.id, c.name;

-- This is intentionally a dedicated login, not a member of any other role.
CREATE ROLE snoop_reader LOGIN PASSWORD :'reader_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE ALL ON DATABASE snoop FROM PUBLIC;
GRANT CONNECT ON DATABASE snoop TO snoop_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO snoop_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, snoop_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, snoop_reader;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, snoop_reader;
GRANT SELECT ON public.customers, public.orders, public.order_summary TO snoop_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER ROLE snoop_reader SET default_transaction_read_only = on;
