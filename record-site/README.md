# HEVI track record site

A static page (`public/index.html`) and two Netlify Functions over a Neon Postgres database.

| Path | What | Database role |
|---|---|---|
| `GET /api/record` | the public record: status, trades and equity since the last paper reset | `hevi_reader` (SELECT only) |
| `POST /api/ingest` | HEVI uploads its journal (`hlbot/record.py`), `Authorization: Bearer <RECORD_TOKEN>` | `hevi_writer` (no DDL, can't touch `ingest_key`) |

- Netlify project `hevi-record`; environment variables `NEON_READER_URL` and `NEON_WRITER_URL`.
- Neon project `hevi-record`, database `hevi`: tables `accounts`, `trades`, `equity`, `ingest_key`.
- The upload token is stored nowhere on the site. `ingest_key` holds only its SHA-256. To rotate it, generate a
  new random token, put it in Railway as `RECORD_TOKEN`, and update the row in the Neon console's SQL editor:
  `UPDATE ingest_key SET sha256 = '<sha256 hex of the new token>', created_at = now() WHERE id = 1;`
- Deploy from this folder (it has its own `netlify.toml` and `package.json`).
