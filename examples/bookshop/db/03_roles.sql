-- querywarden preflight refuses to run unless the role is this limited.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE, TEMPORARY ON DATABASE bookshop FROM PUBLIC;
CREATE ROLE querywarden_reader LOGIN PASSWORD 'demo-only';
GRANT USAGE ON SCHEMA reporting TO querywarden_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO querywarden_reader;
