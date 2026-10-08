import tempfile
import unittest
from pathlib import Path

from sia import archive


def write(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data)


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.old, self.new = self.root / "old", self.root / "new"

    def tearDown(self):
        self._tmp.cleanup()

    def test_tree_hash_depends_on_content_and_ignores_caches(self):
        write(self.old, "a.py", "x = 1\n")
        h = archive.tree_hash(self.old)
        self.assertEqual(len(h), 16)
        write(self.old, "__pycache__/a.cpython.pyc", b"junk")
        write(self.old, "env/task.json.sia-tmp", "tmp")
        self.assertEqual(archive.tree_hash(self.old), h)
        write(self.old, "a.py", "x = 2\n")
        self.assertNotEqual(archive.tree_hash(self.old), h)

    def test_diff_trees_added_removed_modified(self):
        write(self.old, "same.py", "s\n")
        write(self.old, "mod.py", "a\nb\n")
        write(self.old, "gone.py", "g\n")
        write(self.new, "same.py", "s\n")
        write(self.new, "mod.py", "a\nc\n")
        write(self.new, "new.py", "n1\nn2\n")
        patch, stats = archive.diff_trees(self.old, self.new)
        self.assertEqual(stats["added"], ["new.py"])
        self.assertEqual(stats["removed"], ["gone.py"])
        self.assertEqual(stats["modified"], ["mod.py"])
        self.assertEqual(stats["lines_added"], 3)  # c, n1, n2
        self.assertEqual(stats["lines_removed"], 2)  # b, g
        self.assertIn("+++ b/new.py", patch)
        self.assertNotIn("same.py", patch)

    def test_diff_trees_missing_old_and_binary(self):
        write(self.new, "blob.bin", b"\xff\xfe\x00")
        patch, stats = archive.diff_trees(self.root / "missing", self.new)
        self.assertEqual(stats["added"], ["blob.bin"])
        self.assertIn("<binary file, 3 bytes>", patch)

    def test_identical_trees(self):
        write(self.old, "a.py", "x\n")
        write(self.new, "a.py", "x\n")
        patch, stats = archive.diff_trees(self.old, self.new)
        self.assertEqual(patch, "")
        self.assertEqual(stats["added"] + stats["removed"] + stats["modified"], [])


if __name__ == "__main__":
    unittest.main()
