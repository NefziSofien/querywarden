-- Synthetic demo data, written for this project. All people and books are invented.
CREATE TABLE public.customers (
    id int PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL,
    phone text,
    country text NOT NULL,
    signed_up_on date NOT NULL
);
CREATE TABLE public.authors (
    id int PRIMARY KEY,
    name text NOT NULL
);
CREATE TABLE public.books (
    id int PRIMARY KEY,
    title text NOT NULL,
    author_id int NOT NULL REFERENCES public.authors,
    genre text NOT NULL,
    price numeric(6, 2) NOT NULL,
    published_year int NOT NULL
);
CREATE TABLE public.orders (
    id int PRIMARY KEY,
    customer_id int NOT NULL REFERENCES public.customers,
    ordered_on date NOT NULL,
    status text NOT NULL CHECK (status IN ('paid', 'refunded'))
);
CREATE TABLE public.order_items (
    order_id int NOT NULL REFERENCES public.orders,
    book_id int NOT NULL REFERENCES public.books,
    quantity int NOT NULL CHECK (quantity > 0),
    unit_price numeric(6, 2) NOT NULL,
    PRIMARY KEY (order_id, book_id)
);

-- Approved reporting layer. Personal data stays out of these views...
CREATE SCHEMA reporting;
CREATE VIEW reporting.customers AS SELECT id, name, country, signed_up_on FROM public.customers;
CREATE VIEW reporting.authors AS SELECT id, name FROM public.authors;
CREATE VIEW reporting.books AS SELECT id, title, author_id, genre, price, published_year FROM public.books;
CREATE VIEW reporting.orders AS SELECT id, customer_id, ordered_on, status FROM public.orders;
CREATE VIEW reporting.order_items AS SELECT order_id, book_id, quantity, unit_price FROM public.order_items;
-- ...except this one, a realistic mistake that database permissions alone do not catch.
CREATE VIEW reporting.support_contacts AS SELECT id, name, lower(email) AS contact FROM public.customers;
