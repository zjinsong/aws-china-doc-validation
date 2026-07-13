import tempfile
import unittest
import sqlite3
from pathlib import Path

from china_doc_truthkeeper.knowledge_base import KnowledgeBase


class KnowledgeBaseTests(unittest.TestCase):
    def test_save_and_search_check(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = KnowledgeBase(Path(directory) / "truthkeeper.db")
            kb.save_check("dynamodb", "global tables", "cn-north-1", "available", {"source": "test"})
            result = kb.search("dynamo")
        self.assertEqual(result[0]["feature"], "global tables")
        self.assertEqual(result[0]["evidence"]["source"], "test")

    def test_does_not_create_feedback_drafts_table(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "truthkeeper.db"
            KnowledgeBase(database_path)
            connection = sqlite3.connect(database_path)
            try:
                row = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_drafts'"
                ).fetchone()
            finally:
                connection.close()
        self.assertIsNone(row)


if __name__ == "__main__": unittest.main()
