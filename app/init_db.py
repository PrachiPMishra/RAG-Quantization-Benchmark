from db import get_connection

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    chunk_text TEXT NOT NULL,
    embedding vector(384),
    chunk_index INTEGER NOT NULL
);
"""


def main() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    print("Schema applied.")


if __name__ == "__main__":
    main()
