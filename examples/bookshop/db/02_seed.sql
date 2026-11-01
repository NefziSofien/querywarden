INSERT INTO public.customers VALUES
    (1, 'Amina Haddad', 'canary-7f3a@example.test', '+216 00 000 001', 'Tunisia', '2023-02-10'),
    (2, 'Louis Martin', 'louis@example.test', '+33 0 00 00 00 02', 'France', '2023-06-21'),
    (3, 'Nour Ben Ali', 'nour@example.test', NULL, 'Tunisia', '2024-01-15'),
    (4, 'Clara Dubois', 'clara@example.test', '+33 0 00 00 00 04', 'France', '2024-03-02'),
    (5, 'Sami Trabelsi', 'sami@example.test', '+216 00 000 005', 'Tunisia', '2024-07-30'),
    (6, 'Eva Schmidt', 'eva@example.test', NULL, 'Germany', '2024-11-11'),
    (7, 'Omar Khelifi', 'omar@example.test', '+216 00 000 007', 'Tunisia', '2025-01-05'),
    (8, 'Hannah Vogel', 'hannah@example.test', NULL, 'Germany', '2025-02-14');

INSERT INTO public.authors VALUES
    (1, 'Maya Solis'),
    (2, 'Tomas Reed'),
    (3, 'Lina Farah'),
    (4, 'Jonas Weber'),
    (5, 'Iris Moreau');

INSERT INTO public.books VALUES
    (1, 'The Salt Road', 1, 'fiction', 18.00, 2019),
    (2, 'Harbor of Glass', 1, 'fiction', 22.50, 2022),
    (3, 'Data for Skeptics', 2, 'technology', 39.00, 2021),
    (4, 'Queries in Practice', 2, 'technology', 45.00, 2023),
    (5, 'A Short History of Maps', 3, 'history', 27.00, 2018),
    (6, 'Empires of Paper', 3, 'history', 31.50, 2024),
    (7, 'The Quiet Garden', 4, 'nature', 15.00, 2020),
    (8, 'Rivers at Night', 4, 'nature', 19.50, 2023),
    (9, 'Small Kitchens', 5, 'cooking', 24.00, 2017),
    (10, 'Unsold Stories', 5, 'fiction', 12.00, 2024);

INSERT INTO public.orders VALUES
    (1, 1, '2025-01-12', 'paid'),
    (2, 2, '2025-01-20', 'paid'),
    (3, 1, '2025-02-03', 'paid'),
    (4, 3, '2025-02-18', 'refunded'),
    (5, 4, '2025-03-05', 'paid'),
    (6, 5, '2025-03-22', 'paid'),
    (7, 2, '2025-04-09', 'paid'),
    (8, 6, '2025-04-27', 'paid'),
    (9, 1, '2025-05-15', 'paid'),
    (10, 7, '2025-06-01', 'paid'),
    (11, 4, '2024-11-20', 'paid'),
    (12, 3, '2024-12-24', 'paid');

INSERT INTO public.order_items VALUES
    (1, 1, 1, 18.00),
    (1, 3, 1, 39.00),
    (2, 4, 2, 45.00),
    (3, 2, 1, 22.50),
    (4, 5, 1, 27.00),
    (5, 3, 1, 35.00),
    (5, 7, 2, 15.00),
    (6, 9, 1, 24.00),
    (6, 1, 1, 18.00),
    (7, 6, 1, 31.50),
    (8, 8, 3, 19.50),
    (9, 4, 1, 45.00),
    (9, 5, 1, 27.00),
    (10, 2, 2, 22.50),
    (11, 1, 1, 16.00),
    (12, 9, 2, 24.00);
