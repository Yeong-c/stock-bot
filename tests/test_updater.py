"""업데이트 모듈 테스트: 로컬 zip(file://)으로 실제 덮어쓰기 검증."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from stockbot import updater


def test_version_compare():
    assert updater.is_newer("1.1.0", "1.0.9")
    assert updater.is_newer("2.0", "1.99.99")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("1.0.0", "1.0.1")


def test_apply_from_local_zip(tmp_path, monkeypatch):
    # 가짜 프로젝트 루트
    root = tmp_path / "proj"
    (root / "stockbot").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "VERSION").write_text("1.0.0", encoding="utf-8")
    (root / "config.yaml").write_text("secret: keep", encoding="utf-8")
    (root / "data" / "state.json").write_text("{}", encoding="utf-8")
    (root / "stockbot" / "bot.py").write_text("OLD", encoding="utf-8")
    (root / "requirements.txt").write_text("a==1", encoding="utf-8")
    monkeypatch.setattr(updater, "ROOT", root)
    monkeypatch.setattr(updater, "VERSION_FILE", root / "VERSION")

    # 새 버전 zip (GitHub 형식: 최상위 폴더 하나)
    src = tmp_path / "dad-stock-bot-main"
    (src / "stockbot").mkdir(parents=True)
    (src / "data").mkdir()
    (src / "VERSION").write_text("1.1.0", encoding="utf-8")
    (src / "config.yaml").write_text("secret: OVERWRITTEN", encoding="utf-8")
    (src / "data" / "state.json").write_text("BAD", encoding="utf-8")
    (src / "stockbot" / "bot.py").write_text("NEW", encoding="utf-8")
    (src / "stockbot" / "newfile.py").write_text("ADDED", encoding="utf-8")
    (src / "requirements.txt").write_text("a==1", encoding="utf-8")  # 변경 없음 → pip 안 돎
    (src / "CHANGELOG.md").write_text("# x\n\n## 1.1.0\n- 첫 줄\n- 둘째 줄\n\n## 1.0.0\n- 옛날\n", encoding="utf-8")
    zpath = tmp_path / "main.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, "dad-stock-bot-main/" + str(f.relative_to(src)).replace("\\", "/"))
    vpath = tmp_path / "VERSION"
    vpath.write_text("1.1.0\n", encoding="utf-8")

    cfg = {"update": {"repo": "x/y", "branch": "main",
                      "version_url": vpath.as_uri(), "zip_url": zpath.as_uri()}}
    info = updater.check(cfg)
    assert info["available"] and info["latest"] == "1.1.0"

    res = updater.apply(cfg)
    assert res["updated"] and res["to"] == "1.1.0"
    assert (root / "stockbot" / "bot.py").read_text(encoding="utf-8") == "NEW"
    assert (root / "stockbot" / "newfile.py").read_text(encoding="utf-8") == "ADDED"
    assert (root / "config.yaml").read_text(encoding="utf-8") == "secret: keep"        # 보호
    assert (root / "data" / "state.json").read_text(encoding="utf-8") == "{}"          # 보호
    assert res["notes"] == ["첫 줄", "둘째 줄"]
    assert res["pip"] == ""
    # 백업 생성
    backups = list((root / "backup").iterdir())
    assert len(backups) == 1 and (backups[0] / "stockbot" / "bot.py").read_text(encoding="utf-8") == "OLD"
    # 다시 적용하면 최신이라 안 함
    assert updater.apply(cfg)["updated"] is False
