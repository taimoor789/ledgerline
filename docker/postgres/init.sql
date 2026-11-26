-- Dev only: the API logs in as ledger_api, a member of ledger_writer.
CREATE ROLE ledger_owner NOLOGIN;
CREATE ROLE ledger_writer NOLOGIN;
CREATE ROLE ledger_api LOGIN PASSWORD 'ledger_api' IN ROLE ledger_writer;
