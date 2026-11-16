-- Disposable POC credentials, never used outside the test database.
CREATE ROLE app_runtime LOGIN PASSWORD 'poc-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_runtime;
