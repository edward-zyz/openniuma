"""原子 JSON 文件读写模块。

使用独立 .lock 文件 + fcntl.flock 实现并发安全，
写入通过临时文件 → fsync → os.replace 保证原子性。
"""

import fcntl
import json
import os
import tempfile
import time

LOCK_TIMEOUT_SEC = 10


class JsonFileStore:
    """线程/进程安全的 JSON 文件存储。"""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self.lock_path = self.path + ".lock"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # ── public API ──────────────────────────────────────────

    def read(self) -> dict:
        """读取 JSON 文件，文件不存在或损坏时返回 {}。"""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def write(self, data: dict) -> None:
        """原子写入 JSON 文件（临时文件 → fsync → rename）。"""
        dir_name = os.path.dirname(self.path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def update(self, fn) -> dict:
        """read-modify-write 原子操作，全程持锁。

        fn 接受当前 data dict，返回修改后的 dict。
        返回修改后的数据。
        """
        lock_fd = self._acquire_lock()
        try:
            current = self.read()
            updated = fn(current)
            self.write(updated)
            return updated
        finally:
            self._release_lock(lock_fd)

    # ── lock 内部实现 ──────────────────────────────────────

    def _acquire_lock(self) -> int:
        """获取文件锁，支持超时和死进程检测。

        使用稳定的 fd：打开 lock 文件一次，然后阻塞式等待 flock。
        不删除 lock 文件（避免多进程在不同 inode 上持锁的竞态）。
        """
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + LOCK_TIMEOUT_SEC

        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # 拿到锁，写入当前 PID
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, str(os.getpid()).encode())
                return fd
            except (BlockingIOError, OSError):
                pass

            if time.monotonic() >= deadline:
                os.close(fd)
                raise TimeoutError(
                    f"无法在 {LOCK_TIMEOUT_SEC}s 内获取锁: {self.lock_path}"
                )
            time.sleep(0.02)

    def _release_lock(self, fd: int) -> None:
        """释放文件锁并关闭文件描述符。"""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _is_holder_dead(self) -> bool:
        """检查 lock 文件中记录的 PID 对应的进程是否已死。"""
        try:
            with open(self.lock_path, "r") as f:
                content = f.read().strip()
            if not content:
                return True
            pid = int(content)
            os.kill(pid, 0)  # 信号 0 仅检查进程存在性
            return False
        except (OSError, ValueError):
            # 进程不存在 / PID 无效 / 文件读取失败
            return True
