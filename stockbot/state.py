"""봇 상태 저장 (연결된 채팅, 감시 종목, 기준값, 알림 이력). JSON 파일 하나."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import DATA_DIR


class State:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA_DIR / "state.json")
        self._lock = threading.RLock()
        self.data: dict[str, Any] = {
            "chat_ids": [],
            "watchlist": [],
            "baselines": {},      # code -> {"mean":..., "bars":..., "from":..., "to":..., "source":..., "built":...}
            "minute_alerts": {},  # date -> [ {...} ]
            "pending": {},        # chat_id -> action
            "last_error_sent": {},
        }
        self._mtime = 0.0
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    with open(self.path, encoding="utf-8") as f:
                        loaded = json.load(f)
                    self.data.update(loaded)
                    self._mtime = self.path.stat().st_mtime
                except Exception:
                    pass

    def refresh_if_changed(self) -> bool:
        """봇과 데스크톱 창이 같은 파일을 쓰므로, 다른 쪽이 바꿨으면 다시 읽는다."""
        try:
            m = self.path.stat().st_mtime
        except OSError:
            return False
        if m > self._mtime:
            self.load()
            return True
        return False

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                pass

    # ---- chat ----
    @property
    def chat_ids(self) -> list[int]:
        return list(self.data.get("chat_ids", []))

    def add_chat(self, chat_id: int) -> None:
        with self._lock:
            self.refresh_if_changed()
            if chat_id not in self.data["chat_ids"]:
                self.data["chat_ids"].append(chat_id)
                self.save()

    # ---- watchlist ----
    @property
    def watchlist(self) -> list[str]:
        return list(self.data.get("watchlist", []))

    def set_watchlist(self, codes: list[str]) -> None:
        with self._lock:
            self.data["watchlist"] = list(dict.fromkeys(codes))
            self.save()

    def add_watch(self, code: str) -> bool:
        with self._lock:
            self.refresh_if_changed()
            if code in self.data["watchlist"]:
                return False
            self.data["watchlist"].append(code)
            self.save()
            return True

    def remove_watch(self, code: str) -> bool:
        with self._lock:
            self.refresh_if_changed()
            if code not in self.data["watchlist"]:
                return False
            self.data["watchlist"].remove(code)
            self.data["baselines"].pop(code, None)
            self.save()
            return True

    # ---- baselines ----
    def get_baseline(self, code: str) -> dict | None:
        return self.data.get("baselines", {}).get(code)

    def set_baseline(self, code: str, info: dict) -> None:
        with self._lock:
            self.refresh_if_changed()
            self.data.setdefault("baselines", {})[code] = info
            self.save()

    # ---- minute alerts ----
    def add_minute_alert(self, date: str, rec: dict) -> None:
        with self._lock:
            self.refresh_if_changed()
            self.data.setdefault("minute_alerts", {}).setdefault(date, []).append(rec)
            # 오래된 날짜 정리 (30일)
            keys = sorted(self.data["minute_alerts"].keys())
            for k in keys[:-30]:
                self.data["minute_alerts"].pop(k, None)
            self.save()

    def minute_alerts(self, date: str) -> list[dict]:
        return list(self.data.get("minute_alerts", {}).get(date, []))

    # ---- pending action per chat ----
    def set_pending(self, chat_id: int, action: str | None) -> None:
        with self._lock:
            if action is None:
                self.data.setdefault("pending", {}).pop(str(chat_id), None)
            else:
                self.data.setdefault("pending", {})[str(chat_id)] = action
            self.save()

    def get_pending(self, chat_id: int) -> str | None:
        return self.data.get("pending", {}).get(str(chat_id))
