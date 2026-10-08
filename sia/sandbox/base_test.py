import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from sia.sandbox.base import Sandbox, SandboxPaths


class FakeSandbox(Sandbox):
    """In-memory backend: records commands and serves scripted exec results."""

    def __init__(self):
        self.paths = SandboxPaths(agent_home="/home", env_dir="/env", workspace="/ws", runtime="/rt")
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.results: list[tuple[int, str]] = []

    def setup(self):
        pass

    def teardown(self):
        pass

    def exec(self, cmd, timeout=120):
        self.commands.append(cmd)
        return self.results.pop(0) if self.results else (0, "")

    def write_bytes(self, path, data):
        self.files[path] = data

    def read_bytes(self, path):
        return self.files.get(path)

    def proxy_url(self, port):
        return f"http://h:{port}"


class SandboxPathsTest(unittest.TestCase):
    def test_harness_path(self):
        self.assertEqual(SandboxPaths("/a", "/e", "/w", "/r").harness, "/a/harness")


class SandboxHelpersTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeSandbox()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_abstract(self):
        with self.assertRaises(TypeError):
            Sandbox()

    def test_check(self):
        self.sb.results = [(0, "ok"), (2, "bad")]
        self.assertEqual(self.sb.check("true"), "ok")
        with self.assertRaisesRegex(RuntimeError, r"failed \(2\): false\nbad"):
            self.sb.check("false")

    def test_write_text_atomic_and_read_text(self):
        self.sb.write_text_atomic("/env/my file.json", "héllo")
        self.assertEqual(self.sb.files["/env/my file.json.sia-tmp"], "héllo".encode())
        self.assertEqual(self.sb.commands, ["mv -f '/env/my file.json.sia-tmp' '/env/my file.json'"])
        self.sb.files["/x"] = b"\xffok"
        self.assertEqual(self.sb.read_text("/x"), "�ok")
        self.assertIsNone(self.sb.read_text("/missing"))

    def test_make_dirs(self):
        self.sb.make_dirs()
        self.assertEqual(self.sb.commands, ["mkdir -p /home /env /ws /rt"])

    def test_upload_dir(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "f.txt").write_text("data")
        self.sb.upload_dir(self.root, "/dest")
        with tarfile.open(fileobj=io.BytesIO(self.sb.files["/rt/upload.tgz"]), mode="r:gz") as tar:
            self.assertEqual(tar.extractfile("./sub/f.txt").read(), b"data")
        self.assertEqual(self.sb.commands[-1], "mkdir -p /dest && tar -xzf /rt/upload.tgz -C /dest && rm -f /rt/upload.tgz")
        self.sb.upload_dir(self.root, "/dest", replace=True)
        self.assertTrue(self.sb.commands[-1].startswith("rm -rf /dest && mkdir -p /dest"))

    def test_download_dir(self):
        buf = io.BytesIO()
        src = self.root / "src"
        src.mkdir()
        (src / "a.txt").write_text("A")
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(src, arcname=".")
        self.sb.files["/rt/download.tgz"] = buf.getvalue()
        out = self.root / "out"
        self.assertTrue(self.sb.download_dir("/remote", out, exclude=("__pycache__",)))
        self.assertEqual((out / "a.txt").read_text(), "A")
        self.assertIn("--exclude=__pycache__", self.sb.commands[0])
        self.assertEqual(self.sb.commands[1], "rm -f /rt/download.tgz")

    def test_download_missing_dir(self):
        self.sb.results = [(1, "")]
        self.assertFalse(self.sb.download_dir("/nope", self.root / "out"))
        self.assertFalse((self.root / "out").exists())

    def test_start_harness(self):
        self.sb.start_harness({"A": "x y", "B": "1"}, "/rt/log.txt")
        cmd = self.sb.commands[0]
        self.assertTrue(cmd.startswith("rm -f /rt/exit_code; setsid sh -c "))
        self.assertIn("echo $! > /rt/harness.pid", cmd)
        self.assertIn("/home/harness/start.sh", cmd)
        self.assertIn("A=", cmd)
        self.assertIn("/rt/exit_code", cmd)

    def test_start_harness_failure_raises(self):
        self.sb.results = [(1, "err")]
        with self.assertRaises(RuntimeError):
            self.sb.start_harness({}, "/rt/log")

    def test_poll_harness(self):
        self.sb.results = [(0, "running\n"), (0, "lost\n"), (0, ""), (0, "0\n75\n"), (0, "143")]
        self.assertEqual([self.sb.poll_harness() for _ in range(5)], [None, -1, -1, 75, 143])

    def test_stop_harness(self):
        self.sb.results = [(1, "")]  # never raises
        self.sb.stop_harness()
        self.assertIn("kill -TERM -- -$pid", self.sb.commands[0])
        self.assertIn("/rt/harness.pid", self.sb.commands[0])


if __name__ == "__main__":
    unittest.main()
