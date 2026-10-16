import os
import sys

# Ensure project root is in sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import json
import unittest
from unittest.mock import MagicMock, patch
import faiss
import numpy as np

import database

from database import (
    User,
    ChatSession,
    ChatMessage,
    DocumentChunk,
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    init_db,
    SessionLocal,
)


class TestNeonDatabaseAndAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_available = init_db()

    def test_01_password_hashing(self):
        password = "SecurePassword123!"
        hashed = hash_password(password)
        self.assertNotEqual(password, hashed)
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))

    def test_02_jwt_token_flow(self):
        payload = {"sub": "user_12345", "email": "tester@example.com"}
        token = create_access_token(payload)
        self.assertIsInstance(token, str)
        decoded = decode_access_token(token)
        self.assertEqual(decoded["sub"], "user_12345")
        self.assertEqual(decoded["email"], "tester@example.com")

    def test_03_neon_db_connection_and_tables(self):
        if not self.db_available or not SessionLocal:
            self.skipTest("Neon DB not reachable")
        with SessionLocal() as db:
            # Query document chunks count
            count = db.query(DocumentChunk).count()
            self.assertGreaterEqual(count, 0)

            # Test creating a temporary test session and message
            test_sess_id = f"test_suite_{os.urandom(4).hex()}"
            sess = ChatSession(id=test_sess_id, title="Automated Test Session")
            db.add(sess)
            db.commit()

            msg = ChatMessage(session_id=test_sess_id, role="user", content="Hello RAG Test")
            db.add(msg)
            db.commit()

            # Verify presence
            retrieved = db.query(ChatMessage).filter(ChatMessage.session_id == test_sess_id).first()
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.content, "Hello RAG Test")

            # Clean up
            db.delete(sess)
            db.commit()


class TestFAISSAndPersistence(unittest.TestCase):
    def test_04_faiss_persistence_files_exist(self):
        index_path = os.path.join("data", "faiss_index.bin")
        chunks_path = os.path.join("data", "chunks_cache.json")
        self.assertTrue(os.path.exists(index_path), "faiss_index.bin should exist in data/")
        self.assertTrue(os.path.exists(chunks_path), "chunks_cache.json should exist in data/")

        # Verify index can be read
        index = faiss.read_index(index_path)
        self.assertGreater(index.ntotal, 0)

        with open(chunks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertIn("chunks", data)
            self.assertEqual(len(data["chunks"]), index.ntotal)


class TestGroqAndOnlineFallback(unittest.TestCase):
    def test_05_mocked_groq_answer_generation(self):
        import app

        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Operating System acts as an intermediary between users and computer hardware."
        mock_client.chat.completions.create.return_value.choices = [mock_choice]

        with patch("app.get_groq_client", return_value=mock_client):
            answer = app.generate_answer(["Operating System manages hardware."], "What is an OS?")
            self.assertIn("intermediary", answer)

    def test_06_online_knowledge_fetch(self):
        import app

        result = app.fetch_online_knowledge("what is Docker")
        self.assertIsNotNone(result)
        self.assertIn("title", result)
        self.assertIn("text", result)
        self.assertTrue(len(result["text"]) > 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)

