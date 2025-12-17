import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


def _is_pytest() -> bool:
    import sys

    return bool(os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules)


if __name__ == "__main__" and not _is_pytest():
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )

    with driver.session() as s:
        print(s.run("RETURN 1 AS ok").single()["ok"])
