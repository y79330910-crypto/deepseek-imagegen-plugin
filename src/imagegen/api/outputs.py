"""进程内 Output Registry：generation_id → 生成文件路径（仅内存，重启即失）。"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional


class OutputRegistry:
    """最近 N 条生成结果的内存注册表；不是 History，不写数据库。"""

    def __init__(self, max_entries: int = 256):
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max(1, int(max_entries))

    def register(self, generation_id: str, path: str) -> None:
        key = str(generation_id)
        with self._lock:
            self._entries[key] = str(path)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def get(self, generation_id: str) -> Optional[str]:
        with self._lock:
            return self._entries.get(str(generation_id))

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
