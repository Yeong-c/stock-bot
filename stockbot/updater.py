"""자체 업데이트 — GitHub 저장소의 최신 코드를 받아 덮어쓴다.

설정(config.yaml):
  update:
    repo: "깃허브아이디/dad-stock-bot"   # 공개 저장소
    branch: "main"
버전은 저장소의 VERSION 파일(예: 1.1.0)로 비교한다.
보호(덮어쓰지 않음): config.yaml, data/, logs/, .venv/, backup/
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .config import ROOT

log = logging.getLogger("stockbot.updater")

VERSION_FILE = ROOT / "VERSION"
PROTECTED = {"config.yaml", "data", "logs", ".venv", "backup", ".git", "state.json"}


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _vtuple(v: str) -> tuple[int, ...]:
    out = []
    for part in v.strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    return _vtuple(latest) > _vtuple(current)


def urls(cfg: dict) -> tuple[str, str] | None:
    u = cfg.get("update", {}) or {}
    repo = str(u.get("repo", "")).strip()
    if not repo or "/" not in repo or "깃허브" in repo:
        return None
    if u.get("version_url") and u.get("zip_url"):  # 테스트/수동 지정
        return str(u["version_url"]), str(u["zip_url"])
    branch = str(u.get("branch", "main") or "main")
    return (f"https://raw.githubusercontent.com/{repo}/{branch}/VERSION",
            f"https://github.com/{repo}/archive/refs/heads/{branch}.zip")


_HEADERS = {"User-Agent": "dad-stock-bot-updater", "Cache-Control": "no-cache", "Pragma": "no-cache"}


def _fetch(url: str, timeout: int = 60) -> bytes:
    """requests(certifi 인증서 번들) 로 받는다. 윈도우 기본 SSL 저장소가 GitHub 다운로드 서버를 못 검증하는 문제 회피.
    file:// (테스트) 는 urllib."""
    if url.startswith("file:"):
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    import requests
    r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.content


def _latest_version(cfg: dict, version_url: str) -> str:
    """GitHub API(캐시 없음) → 실패 시 raw 파일(캐시 우회 파라미터)."""
    u = cfg.get("update", {}) or {}
    repo = str(u.get("repo", "")).strip()
    branch = str(u.get("branch", "main") or "main")
    if not version_url.startswith("file:") and repo and not u.get("version_url"):
        try:
            import base64
            import json as _json
            data = _json.loads(_fetch(f"https://api.github.com/repos/{repo}/contents/VERSION?ref={branch}", timeout=20))
            v = base64.b64decode(data["content"]).decode("utf-8").strip()
            if v:
                return v
        except Exception as e:  # noqa: BLE001
            log.debug("GitHub API 버전 확인 실패, raw 로 대체: %s", e)
    sep = "&" if "?" in version_url else "?"
    bust = "" if version_url.startswith("file:") else f"{sep}nocache={int(time.time())}"
    return _fetch(version_url + bust, timeout=20).decode("utf-8").strip()


def check(cfg: dict) -> dict:
    """{'current','latest','available','error'}"""
    cur = current_version()
    u = urls(cfg)
    if u is None:
        return {"current": cur, "latest": None, "available": False, "error": "업데이트 주소(update.repo)가 설정되지 않았습니다"}
    try:
        latest = _latest_version(cfg, u[0])
    except Exception as e:  # noqa: BLE001
        return {"current": cur, "latest": None, "available": False, "error": f"확인 실패: {e}"}
    return {"current": cur, "latest": latest, "available": is_newer(latest, cur), "error": None}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def latest_notes(path: Path | None = None, max_lines: int = 6) -> list[str]:
    """CHANGELOG.md 의 맨 위 버전 항목 bullet 들."""
    path = path or (ROOT / "CHANGELOG.md")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out, started = [], False
    for ln in lines:
        if ln.startswith("## "):
            if started:
                break
            started = True
            continue
        if started and ln.strip().startswith("- "):
            out.append(ln.strip()[2:])
        if len(out) >= max_lines:
            break
    return out


def apply(cfg: dict, progress=None) -> dict:
    """최신 zip 을 받아 덮어쓴다. 반환 {'updated', 'from', 'to', 'pip', 'notes', 'error'}"""
    def say(m):
        log.info(m)
        if progress:
            progress(m)

    info = check(cfg)
    if info.get("error"):
        return {"updated": False, "error": info["error"], "from": info["current"], "to": info["latest"]}
    if not info["available"]:
        return {"updated": False, "error": None, "from": info["current"], "to": info["latest"], "notes": []}
    u = urls(cfg)
    say(f"새 버전 {info['latest']} 내려받는 중…")
    try:
        data = _fetch(u[1], timeout=120)
    except Exception as e:  # noqa: BLE001
        return {"updated": False, "error": f"내려받기 실패: {e}", "from": info["current"], "to": info["latest"]}

    req_before = _sha(ROOT / "requirements.txt")
    backup = ROOT / "backup" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    say("파일 교체 중…")
    copied = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        top = names[0].split("/")[0] + "/" if names and "/" in names[0] else ""
        for n in names:
            if n.endswith("/"):
                continue
            rel = n[len(top):] if top and n.startswith(top) else n
            if not rel:
                continue
            first = rel.split("/")[0]
            if first in PROTECTED or rel.startswith(".github"):
                continue
            dest = ROOT / rel
            if dest.exists():
                b = backup / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, b)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(n))
            copied += 1
    # 오래된 백업은 3개만 유지
    bdir = ROOT / "backup"
    for old in sorted(bdir.iterdir())[:-3]:
        shutil.rmtree(old, ignore_errors=True)

    pip_msg = ""
    if _sha(ROOT / "requirements.txt") != req_before:
        say("라이브러리 설치 중… (1~3분)")
        py = Path(sys.executable)
        try:
            r = subprocess.run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "-q"],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=600)
            pip_msg = "라이브러리 갱신 완료" if r.returncode == 0 else f"라이브러리 설치 오류: {r.stderr[-300:]}"
        except Exception as e:  # noqa: BLE001
            pip_msg = f"라이브러리 설치 오류: {e}"
    new_ver = current_version()
    say(f"업데이트 완료: {info['current']} → {new_ver} ({copied}개 파일)")
    return {"updated": True, "error": None, "from": info["current"], "to": new_ver, "files": copied,
            "pip": pip_msg, "notes": latest_notes()}
