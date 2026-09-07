"""아빠용 실행 창 — 바탕화면 아이콘으로 켜고, '종료' 버튼으로 끈다.

- 창이 뜨면 봇을 자동으로 켠다. 큰 글씨로 '실행 중' / '꺼짐' 표시.
- 봇이 죽으면 15초 후 자동으로 다시 켠다 (토큰 없음/중복 실행이면 재시작 안 함).
- 창을 닫으면(X) 봇도 같이 끌지 물어본다.
사용: pythonw launcher.py   (launcher.vbs 가 이렇게 실행함)
점검: python launcher.py --selftest
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
LOG = ROOT / "logs" / "bot.log"
CONSOLE = ROOT / "logs" / "console.log"
WIN = os.name == "nt"
PY = ROOT / (".venv/Scripts/python.exe" if WIN else ".venv/bin/python")


class BotProcess:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.stopping = False
        self.exit_code: int | None = None
        self.started_at: dt.datetime | None = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if self.alive():
            return
        (ROOT / "logs").mkdir(exist_ok=True)
        out = open(CONSOLE, "a", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if WIN else 0
        self.proc = subprocess.Popen(
            [str(PY), "-m", "stockbot"], cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
            creationflags=flags, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self.stopping = False
        self.exit_code = None
        self.started_at = dt.datetime.now()

    def stop(self) -> None:
        self.stopping = True
        if not self.alive():
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.exit_code = self.proc.returncode

    def poll(self) -> int | None:
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is not None:
            self.exit_code = rc
        return rc


def last_log_line() -> str:
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4000))
            lines = f.read().decode("utf-8", errors="ignore").strip().splitlines()
        for ln in reversed(lines):
            if " INFO " in ln or " WARNING " in ln:
                return ln.split(": ", 1)[-1][:60]
    except OSError:
        pass
    return ""


def _load_cfg():
    sys.path.insert(0, str(ROOT))
    from stockbot.config import load_config
    return load_config()


def run_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox

    sys.path.insert(0, str(ROOT))
    from stockbot import updater

    bot = BotProcess()
    root = tk.Tk()
    root.title(f"아빠 주식 알림봇  v{updater.current_version()}")
    root.geometry("440x320")
    root.resizable(False, False)
    try:
        ico = ROOT / "bot.ico"
        if WIN and ico.exists():
            root.iconbitmap(str(ico))
    except Exception:  # noqa: BLE001
        pass

    status = tk.Label(root, text="시작하는 중…", font=("맑은 고딕", 22, "bold"), fg="#444")
    status.pack(pady=(24, 4))
    detail = tk.Label(root, text="", font=("맑은 고딕", 11), fg="#666")
    detail.pack()
    info = tk.Label(root, text="", font=("맑은 고딕", 10), fg="#888", wraplength=380)
    info.pack(pady=(6, 0))

    notice = tk.Label(root, text="", font=("맑은 고딕", 10, "bold"), fg="#1a5fb4")
    notice.pack(pady=(4, 0))

    btns = tk.Frame(root)
    btns.pack(pady=14)
    restart_at: list[float] = [0.0]
    update_state = {"latest": None, "busy": False}

    def do_start():
        bot.start()
        refresh()

    def do_stop():
        bot.stop()
        refresh()

    start_btn = tk.Button(btns, text="켜기", width=10, font=("맑은 고딕", 13), command=do_start)
    stop_btn = tk.Button(btns, text="종료", width=10, font=("맑은 고딕", 13), command=do_stop, fg="#b00")
    start_btn.grid(row=0, column=0, padx=6)
    stop_btn.grid(row=0, column=1, padx=6)

    def relaunch_self():
        pyw = ROOT / (".venv/Scripts/pythonw.exe" if WIN else ".venv/bin/python")
        subprocess.Popen([str(pyw), str(ROOT / "launcher.py")], cwd=str(ROOT))
        root.destroy()

    def do_update():
        if update_state["busy"]:
            return
        update_state["busy"] = True
        update_btn.config(state="disabled")
        notice.config(text="새 버전 확인 중…")

        def work():
            try:
                cfg = _load_cfg()
                info = updater.check(cfg)
                if info.get("error"):
                    root.after(0, lambda: finish(f"업데이트 확인 실패: {info['error']}", None))
                    return
                if not info["available"]:
                    root.after(0, lambda: finish(f"이미 최신 버전입니다 (v{info['current']})", None))
                    return
                root.after(0, lambda: notice.config(text=f"v{info['latest']} 받는 중… 봇을 잠시 끕니다"))
                bot.stop()
                res = updater.apply(cfg, progress=lambda m: root.after(0, lambda m=m: notice.config(text=m)))
                if res.get("updated"):
                    root.after(0, lambda: finish(f"업데이트 완료 v{res['to']} — 창을 다시 엽니다", "relaunch"))
                else:
                    root.after(0, lambda: finish(f"업데이트 실패: {res.get('error')}", "restart"))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: finish(f"업데이트 오류: {e}", "restart"))

        def finish(msg, action):
            update_state["busy"] = False
            update_btn.config(state="normal")
            notice.config(text=msg)
            if action == "relaunch":
                notes = "\n".join("• " + n for n in updater.latest_notes()[:6])
                messagebox.showinfo("업데이트 완료", msg + ("\n\n바뀐 점\n" + notes if notes else ""))
                relaunch_self()
            elif action == "restart":
                bot.start()

        threading.Thread(target=work, daemon=True).start()

    update_btn = tk.Button(btns, text="업데이트", width=10, font=("맑은 고딕", 13), command=do_update)
    update_btn.grid(row=0, column=2, padx=6)

    def check_update_quietly():
        def work():
            try:
                info = updater.check(_load_cfg())
                if info.get("available"):
                    update_state["latest"] = info["latest"]
                    root.after(0, lambda: notice.config(text=f"새 버전 v{info['latest']} 이 있습니다 → '업데이트' 버튼을 누르세요"))
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()
        root.after(6 * 3600 * 1000, check_update_quietly)  # 6시간마다

    def refresh():
        rc = bot.poll()
        if bot.alive():
            status.config(text="● 실행 중", fg="#1a8f3a")
            since = bot.started_at.strftime("%H:%M") if bot.started_at else ""
            detail.config(text=f"{since} 부터 감시하고 있습니다. 이 창은 닫지 마세요.")
            info.config(text=last_log_line())
            start_btn.config(state="disabled")
            stop_btn.config(state="normal")
        else:
            start_btn.config(state="normal")
            stop_btn.config(state="disabled")
            if bot.stopping:
                status.config(text="○ 꺼짐", fg="#999")
                detail.config(text="'켜기'를 누르면 다시 시작합니다.")
                info.config(text="")
            elif rc == 2:
                status.config(text="설정 필요", fg="#b00")
                detail.config(text="config.yaml 에 봇 토큰이 없습니다.")
                info.config(text="")
            elif rc == 3:
                status.config(text="이미 실행 중", fg="#b00")
                detail.config(text="다른 창에서 봇이 이미 켜져 있습니다. 이 창은 닫아도 됩니다.")
                info.config(text="")
            elif rc == 10:  # 텔레그램 /update 로 업데이트됨 → 바로 새 창으로
                status.config(text="업데이트됨", fg="#1a5fb4")
                detail.config(text="새 버전으로 다시 켭니다…")
                root.after(500, relaunch_self)
                return
            elif rc is not None and not update_state["busy"]:
                if restart_at[0] == 0.0:
                    restart_at[0] = time.time() + 15
                left = max(0, int(restart_at[0] - time.time()))
                status.config(text="⟳ 다시 켜는 중", fg="#c60")
                detail.config(text=f"봇이 멈춰서 {left}초 후 자동으로 다시 켭니다.")
                info.config(text=last_log_line())
                if left == 0:
                    restart_at[0] = 0.0
                    bot.start()
            else:
                status.config(text="○ 꺼짐", fg="#999")
        root.after(1000, refresh)

    def on_close():
        if bot.alive():
            if not messagebox.askyesno("종료", "봇을 끄고 창을 닫을까요?\n(끄면 알림이 오지 않습니다)"):
                return
            bot.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, do_start)
    root.after(1200, refresh)
    root.after(3000, check_update_quietly)
    root.mainloop()


def selftest() -> int:
    """GUI 없이 봇을 켰다 끄며 동작 확인."""
    bot = BotProcess()
    bot.start()
    print("started pid", bot.proc.pid)
    for _ in range(20):
        time.sleep(1)
        rc = bot.poll()
        if rc is not None:
            print("bot exited early with code", rc, "(2=토큰없음, 3=이미 실행 중)")
            return rc
    print("alive after 20s, last log:", last_log_line())
    bot.stop()
    print("stopped, code", bot.exit_code)
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    run_gui()
