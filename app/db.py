import os

import psycopg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://quant:quant@localhost:5433/quant_demo"
)


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)
