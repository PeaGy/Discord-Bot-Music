import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import user_memory


class AIChatBlacklistStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_check_remove_and_persist_across_memory_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "memory.db")
            with patch.object(user_memory, "DB_PATH", database):
                await user_memory.init_db()

                self.assertFalse(await user_memory.is_ai_blacklisted(100))
                self.assertTrue(await user_memory.add_ai_blacklist(100, 1))
                self.assertFalse(await user_memory.add_ai_blacklist(100, 1))
                self.assertTrue(await user_memory.is_ai_blacklisted(100))

                # Blacklist là quyền truy cập, không phải trí nhớ hội thoại.
                await user_memory.clear_user(100)
                await user_memory.clear_all()
                self.assertTrue(await user_memory.is_ai_blacklisted(100))

                self.assertTrue(await user_memory.remove_ai_blacklist(100))
                self.assertFalse(await user_memory.remove_ai_blacklist(100))
                self.assertFalse(await user_memory.is_ai_blacklisted(100))


if __name__ == "__main__":
    unittest.main()
