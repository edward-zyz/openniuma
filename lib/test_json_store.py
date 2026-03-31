"""JsonFileStore 单元测试。"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

from openniuma.lib.json_store import JsonFileStore


class TestJsonFileStoreBasic(unittest.TestCase):
    """基础读写测试。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        # 删除文件，让测试从"不存在"状态开始
        os.unlink(self.path)
        self.store = JsonFileStore(self.path)

    def tearDown(self):
        for p in (self.path, self.path + ".lock"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_read_nonexistent_returns_empty_dict(self):
        self.assertEqual(self.store.read(), {})

    def test_write_then_read(self):
        data = {"name": "test", "value": 42}
        self.store.write(data)
        self.assertEqual(self.store.read(), data)

    def test_write_is_atomic_file_exists(self):
        """写入后数据文件必须存在（不是临时文件残留）。"""
        self.store.write({"a": 1})
        self.assertTrue(os.path.exists(self.path))
        # 不应有 .tmp 残留
        dir_name = os.path.dirname(self.path)
        temps = [f for f in os.listdir(dir_name) if f.endswith(".tmp")]
        self.assertEqual(len(temps), 0, f"临时文件残留: {temps}")

    def test_write_preserves_unicode(self):
        data = {"品牌": "星巴克", "emoji": "☕"}
        self.store.write(data)
        with open(self.path, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("星巴克", raw)
        self.assertIn("☕", raw)

    def test_read_corrupted_json_returns_empty(self):
        with open(self.path, "w") as f:
            f.write("{invalid json!!!")
        self.assertEqual(self.store.read(), {})


class TestJsonFileStoreUpdate(unittest.TestCase):
    """update() 原子操作测试。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        os.unlink(self.path)
        self.store = JsonFileStore(self.path)

    def tearDown(self):
        for p in (self.path, self.path + ".lock"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_update_on_empty_file(self):
        result = self.store.update(lambda d: {**d, "key": "value"})
        self.assertEqual(result, {"key": "value"})
        self.assertEqual(self.store.read(), {"key": "value"})

    def test_update_increments(self):
        self.store.write({"count": 0})

        def inc(d):
            d["count"] = d.get("count", 0) + 1
            return d

        self.store.update(inc)
        self.store.update(inc)
        self.store.update(inc)
        self.assertEqual(self.store.read()["count"], 3)

    def test_update_returns_modified_data(self):
        result = self.store.update(lambda d: {"status": "done"})
        self.assertEqual(result, {"status": "done"})


class TestJsonFileStoreConcurrency(unittest.TestCase):
    """并发安全测试。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        os.unlink(self.path)
        self.store = JsonFileStore(self.path)

    def tearDown(self):
        for p in (self.path, self.path + ".lock"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_concurrent_updates_no_lost_writes(self):
        """5 个子进程各递增 count 20 次，最终应为 100。"""
        self.store.write({"count": 0})

        num_procs = 5
        increments_per_proc = 20
        project_root = "/Users/zhangyingze/Documents/AI/POI"

        script = textwrap.dedent(f"""\
            import sys
            sys.path.insert(0, '.')
            from openniuma.lib.json_store import JsonFileStore

            store = JsonFileStore("{self.path}")
            for _ in range({increments_per_proc}):
                store.update(lambda d: {{**d, "count": d.get("count", 0) + 1}})
        """)

        procs = []
        for _ in range(num_procs):
            p = subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            procs.append(p)

        for p in procs:
            stdout, stderr = p.communicate(timeout=60)
            self.assertEqual(
                p.returncode,
                0,
                f"子进程失败 (rc={p.returncode}): {stderr.decode()}",
            )

        final = self.store.read()
        self.assertEqual(
            final["count"],
            num_procs * increments_per_proc,
            f"期望 {num_procs * increments_per_proc}，实际 {final.get('count')}",
        )

    def test_dead_process_lock_recovery(self):
        """lock 文件中写入不存在的 PID，验证读取/更新仍能成功。"""
        # 写入一个不存在的 PID 到 lock 文件
        with open(self.store.lock_path, "w") as f:
            f.write("99999999")

        self.store.write({"recovered": True})
        result = self.store.read()
        self.assertTrue(result["recovered"])

        # update 也应能恢复
        with open(self.store.lock_path, "w") as f:
            f.write("99999999")

        result = self.store.update(lambda d: {**d, "updated": True})
        self.assertTrue(result["updated"])


if __name__ == "__main__":
    unittest.main()
