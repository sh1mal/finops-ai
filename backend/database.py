import os

import psycopg
from psycopg.rows import dict_row


def connect(*, autocommit=False):
    return psycopg.connect(
        os.environ.get("FINOPS_DATABASE_URL", "dbname=finops_ai"),
        row_factory=dict_row,
        autocommit=autocommit,
        connect_timeout=5,
    )
