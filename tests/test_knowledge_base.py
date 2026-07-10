import tempfile
import unittest
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


if __name__ == "__main__": unittest.main()
