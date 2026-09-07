"""아빠 주식 알림봇 — 데스크톱 창 (큰 글씨).

바탕화면 아이콘이 이 창을 띄운다. 이 창 하나로:
  홈        : 봇 켜기/종료/업데이트, 오늘 알림 요약
  검색 결과 : 검색식 2~6 결과 보기, 지금 검색
  감시 종목 : 분봉 거래폭발 감시 종목 추가/삭제
  오늘 알림 : 장중 분봉 거래폭발 알림 목록
텔레그램 봇(python -m stockbot)은 별도 프로세스로 켜고 끈다.
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
PYW = ROOT / (".venv/Scripts/pythonw.exe" if WIN else ".venv/bin/python")
sys.path.insert(0, str(ROOT))


# ───────────────────────── 봇 프로세스 ─────────────────────────
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
                return ln.split(": ", 1)[-1][:70]
    except OSError:
        pass
    return ""


# ───────────────────────── GUI ─────────────────────────
def run_gui() -> None:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk

    from stockbot import formatting as F
    from stockbot import updater
    from stockbot.calendar_kr import in_market_hours, now_kst
    from stockbot.config import load_config
    from stockbot.data.universe import fetch_listing, load_latest_listing, resolve_name
    from stockbot.screeners.minute import MinuteMonitor
    from stockbot.screeners.runner import load_latest_result, run_daily_scan
    from stockbot.state import State

    cfg = load_config()
    state = State()
    bot = BotProcess()
    listing = {"df": load_latest_listing()}
    monitor = MinuteMonitor(cfg, state, None, name_of=lambda c: name_of(c))

    root = tk.Tk()
    root.title(f"아빠 주식 알림봇  v{updater.current_version()}")
    root.geometry("1080x720")
    root.minsize(900, 600)
    root.configure(bg="#f4f4f4")
    try:
        if WIN and (ROOT / "bot.ico").exists():
            root.iconbitmap(str(ROOT / "bot.ico"))
    except Exception:  # noqa: BLE001
        pass

    fams = set(tkfont.families())
    FAM = next((f for f in ("맑은 고딕", "Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic", "NanumGothic") if f in fams), "TkDefaultFont")
    size = {"v": 16}

    def fnt(delta=0, bold=False):
        return (FAM, size["v"] + delta, "bold" if bold else "normal")

    def name_of(code: str) -> str:
        df = listing["df"]
        if df is not None:
            hit = df[df["code"] == code]
            if len(hit):
                return str(hit["name"].iloc[0])
        return code

    # ---------- 레이아웃: 왼쪽 메뉴 / 오른쪽 페이지 ----------
    side = tk.Frame(root, bg="#2b3a4a", width=210)
    side.pack(side="left", fill="y")
    side.pack_propagate(False)
    body = tk.Frame(root, bg="#f4f4f4")
    body.pack(side="right", fill="both", expand=True)

    pages: dict[str, tk.Frame] = {}
    menu_btns: dict[str, tk.Button] = {}

    def show(name: str):
        for k, fr in pages.items():
            fr.pack_forget()
        pages[name].pack(fill="both", expand=True, padx=16, pady=12)
        for k, b in menu_btns.items():
            b.config(bg="#4a90d9" if k == name else "#2b3a4a")
        refreshers.get(name, lambda: None)()

    refreshers: dict = {}

    tk.Label(side, text="아빠 주식\n알림봇", font=(FAM, 20, "bold"), fg="white", bg="#2b3a4a").pack(pady=(24, 18))
    for key, label in [("home", "홈"), ("results", "검색 결과"), ("watch", "감시 종목"), ("alerts", "오늘 알림"), ("help", "설명")]:
        b = tk.Button(side, text=label, font=(FAM, 17, "bold"), fg="white", bg="#2b3a4a", activebackground="#4a90d9",
                      activeforeground="white", bd=0, relief="flat", height=2, cursor="hand2",
                      command=lambda k=key: show(k))
        b.pack(fill="x", padx=10, pady=3)
        menu_btns[key] = b

    fs = tk.Frame(side, bg="#2b3a4a")
    fs.pack(side="bottom", pady=16)
    tk.Label(fs, text="글씨", font=(FAM, 12), fg="#cbd5e1", bg="#2b3a4a").grid(row=0, column=0, padx=4)

    def resize(delta):
        size["v"] = max(12, min(26, size["v"] + delta))
        apply_fonts()

    tk.Button(fs, text="작게", font=(FAM, 12), command=lambda: resize(-2), width=5).grid(row=0, column=1, padx=2)
    tk.Button(fs, text="크게", font=(FAM, 12), command=lambda: resize(+2), width=5).grid(row=0, column=2, padx=2)

    scalable: list[tuple[tk.Widget, int, bool]] = []  # (widget, delta, bold)

    def reg(w, delta=0, bold=False):
        scalable.append((w, delta, bold))
        w.configure(font=fnt(delta, bold))
        return w

    def apply_fonts():
        for w, d, b in scalable:
            try:
                w.configure(font=fnt(d, b))
            except tk.TclError:
                pass
        for t in text_widgets:
            t.tag_configure("h", font=fnt(2, True))
            t.tag_configure("b", font=fnt(0, True))
            t.tag_configure("n", font=fnt(0))
            t.tag_configure("s", font=fnt(-3), foreground="#555")
            t.tag_configure("up", font=fnt(0, True), foreground="#c0392b")
            t.tag_configure("down", font=fnt(0, True), foreground="#1f5fbf")

    text_widgets: list[tk.Text] = []

    def make_text(parent):
        fr = tk.Frame(parent, bg="white", bd=1, relief="solid")
        t = tk.Text(fr, wrap="word", bg="white", fg="#111", bd=0, padx=14, pady=10, spacing1=2, spacing3=4, cursor="arrow")
        sb = ttk.Scrollbar(fr, command=t.yview)
        t.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        text_widgets.append(t)
        return fr, t

    def set_text(t: tk.Text, parts: list[tuple[str, str]]):
        t.configure(state="normal")
        t.delete("1.0", "end")
        for txt, tag in parts:
            t.insert("end", txt, tag)
        t.configure(state="disabled")

    def big_button(parent, text, cmd, color="#4a90d9", fg="white", delta=0):
        return reg(tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, activebackground=color,
                             activeforeground=fg, bd=0, relief="flat", padx=18, pady=8, cursor="hand2"), delta, True)

    # ================= 홈 =================
    home = tk.Frame(body, bg="#f4f4f4")
    pages["home"] = home
    status = reg(tk.Label(home, text="시작하는 중…", bg="#f4f4f4", fg="#444"), 10, True)
    status.pack(pady=(30, 4))
    detail = reg(tk.Label(home, text="", bg="#f4f4f4", fg="#555"), 0)
    detail.pack()
    notice = reg(tk.Label(home, text="", bg="#f4f4f4", fg="#1a5fb4", wraplength=700), -1, True)
    notice.pack(pady=(10, 0))
    hb = tk.Frame(home, bg="#f4f4f4")
    hb.pack(pady=22)
    start_btn = big_button(hb, "켜기", lambda: (bot.start(), refresh_status()), "#1a8f3a")
    stop_btn = big_button(hb, "종료", lambda: (bot.stop(), refresh_status()), "#c0392b")
    update_btn = big_button(hb, "업데이트", lambda: do_update(), "#555")
    start_btn.grid(row=0, column=0, padx=10)
    stop_btn.grid(row=0, column=1, padx=10)
    update_btn.grid(row=0, column=2, padx=10)
    reg(tk.Label(home, text="오늘 분봉 거래폭발 알림", bg="#f4f4f4", fg="#222"), 0, True).pack(anchor="w", padx=8, pady=(10, 4))
    home_fr, home_text = make_text(home)
    home_fr.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    restart_at = [0.0]
    update_state = {"busy": False}
    seen_alerts = {"n": -1}

    def relaunch_self():
        subprocess.Popen([str(PYW), str(ROOT / "launcher.py")], cwd=str(ROOT))
        root.destroy()

    def refresh_status():
        rc = bot.poll()
        if bot.alive():
            status.config(text="●  실행 중", fg="#1a8f3a")
            since = bot.started_at.strftime("%H:%M") if bot.started_at else ""
            hours = "장중 감시 중" if in_market_hours(cfg) else "장 시간(09:00~15:30)이 되면 감시합니다"
            detail.config(text=f"{since}부터 켜져 있습니다 · {hours}")
            start_btn.config(state="disabled")
            stop_btn.config(state="normal")
        else:
            start_btn.config(state="normal")
            stop_btn.config(state="disabled")
            if update_state["busy"]:
                status.config(text="업데이트 중", fg="#1a5fb4")
            elif bot.stopping:
                status.config(text="○  꺼짐", fg="#999")
                detail.config(text="'켜기'를 누르면 다시 시작합니다.")
            elif rc == 2:
                status.config(text="설정 필요", fg="#c0392b")
                detail.config(text="config.yaml 에 봇 토큰이 없습니다.")
            elif rc == 3:
                status.config(text="이미 실행 중", fg="#c0392b")
                detail.config(text="다른 창에서 봇이 이미 켜져 있습니다. 이 창은 닫아도 됩니다.")
            elif rc == 10:
                status.config(text="업데이트됨", fg="#1a5fb4")
                detail.config(text="새 버전으로 다시 켭니다…")
                root.after(500, relaunch_self)
                return
            elif rc is not None:
                if restart_at[0] == 0.0:
                    restart_at[0] = time.time() + 15
                left = max(0, int(restart_at[0] - time.time()))
                status.config(text="⟳  다시 켜는 중", fg="#c60")
                detail.config(text=f"봇이 멈춰서 {left}초 후 자동으로 다시 켭니다.  ({last_log_line()})")
                if left == 0:
                    restart_at[0] = 0.0
                    bot.start()
            else:
                status.config(text="○  꺼짐", fg="#999")
        root.after(1000, refresh_status)

    def today_alerts():
        state.refresh_if_changed()
        return state.minute_alerts(now_kst().strftime("%Y-%m-%d"))

    def alert_parts(alerts, limit=None):
        if not alerts:
            return [("아직 알림이 없습니다.\n", "n"), ("장중(09:05~15:30)에 감시 종목의 1분 거래량이 평소의 3배 이상 터지면 여기와 텔레그램에 뜹니다.\n", "s")]
        parts = []
        for a in (alerts[-limit:] if limit else alerts)[::-1]:
            chg = a.get("change_pct", 0)
            parts.append((f"{a['time']}  ", "b"))
            parts.append((f"{a['name']}  ", "h"))
            parts.append((f"{a['multiple']}배  ", "b"))
            parts.append((f"{a['price']:,.0f}원  {chg:+.1f}%\n", "up" if chg >= 0 else "down"))
            parts.append((f"      1분 거래량 {a['minute_volume']:,}주 (평소 {a['baseline']:,.0f}주)\n", "s"))
        return parts

    def refresh_home():
        alerts = today_alerts()
        set_text(home_text, alert_parts(alerts, 8))
        if seen_alerts["n"] != -1 and len(alerts) > seen_alerts["n"]:
            root.bell()
        seen_alerts["n"] = len(alerts)
        root.after(10000, refresh_home)

    refreshers["home"] = lambda: set_text(home_text, alert_parts(today_alerts(), 8))

    # ---------- 업데이트 ----------
    def do_update():
        if update_state["busy"]:
            return
        update_state["busy"] = True
        update_btn.config(state="disabled")
        notice.config(text="새 버전 확인 중…")

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

        def work():
            try:
                c = load_config()
                info = updater.check(c)
                if info.get("error"):
                    root.after(0, lambda: finish(f"업데이트 확인 실패: {info['error']}", None)); return
                if not info["available"]:
                    root.after(0, lambda: finish(f"이미 최신 버전입니다 (v{info['current']})", None)); return
                root.after(0, lambda: notice.config(text=f"v{info['latest']} 받는 중… 봇을 잠시 끕니다"))
                bot.stop()
                res = updater.apply(c, progress=lambda m: root.after(0, lambda m=m: notice.config(text=m)))
                if res.get("updated"):
                    root.after(0, lambda: finish(f"업데이트 완료 v{res['to']} — 창을 다시 엽니다", "relaunch"))
                else:
                    root.after(0, lambda: finish(f"업데이트 실패: {res.get('error')}", "restart"))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: finish(f"업데이트 오류: {e}", "restart"))

        threading.Thread(target=work, daemon=True).start()

    def check_update_quietly():
        def work():
            try:
                info = updater.check(load_config())
                if info.get("available"):
                    root.after(0, lambda: notice.config(text=f"새 버전 v{info['latest']} 이 있습니다 → '업데이트' 버튼을 누르세요"))
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()
        root.after(6 * 3600 * 1000, check_update_quietly)

    # ================= 검색 결과 =================
    res_page = tk.Frame(body, bg="#f4f4f4")
    pages["results"] = res_page
    top = tk.Frame(res_page, bg="#f4f4f4")
    top.pack(fill="x")
    res_title = reg(tk.Label(top, text="검색 결과", bg="#f4f4f4", fg="#222"), 4, True)
    res_title.pack(side="left")
    scan_btn = big_button(top, "지금 검색", lambda: do_scan(), "#1a8f3a", delta=-1)
    scan_btn.pack(side="right")
    scan_msg = reg(tk.Label(top, text="", bg="#f4f4f4", fg="#1a5fb4"), -2)
    scan_msg.pack(side="right", padx=12)
    bar = tk.Frame(res_page, bg="#f4f4f4")
    bar.pack(fill="x", pady=8)
    sel = {"key": "bottom_accum"}
    sbtns: dict[str, tk.Button] = {}
    for key, label in F.SCREENER_ORDER:
        if not cfg["screeners"].get(key, {}).get("enabled", True):
            continue
        clean = label.split(" ", 1)[1] if " " in label else label
        b = reg(tk.Button(bar, text=clean, command=lambda k=key: pick(k), bg="#e5e7eb", fg="#222", bd=0, relief="flat",
                          padx=10, pady=6, cursor="hand2", activebackground="#cbd5e1"), -2, True)
        b.pack(side="left", padx=3)
        sbtns[key] = b
    res_fr, res_text = make_text(res_page)
    res_fr.pack(fill="both", expand=True)

    def pick(key):
        sel["key"] = key
        render_results()

    def signal_parts(sig: dict, i: int):
        chg = sig.get("change_pct", 0)
        return [(f"{i}. {sig['name']} ", "h"), (f"({sig['code']})  ", "s"),
                (f"{sig['price']:,.0f}원  ", "b"), (f"{chg:+.1f}%\n", "up" if chg >= 0 else "down")] + \
               [(f"      · {ln}\n", "s") for ln in sig.get("lines", [])]

    def render_results():
        out = load_latest_result()
        for k, b in sbtns.items():
            cnt = ""
            if out:
                r = out.get("results", {}).get(k)
                cnt = f" ({r['count']})" if r else ""
            base = dict(F.SCREENER_ORDER)[k]
            base = base.split(" ", 1)[1] if " " in base else base
            b.config(text=base + cnt, bg="#4a90d9" if k == sel["key"] else "#e5e7eb", fg="white" if k == sel["key"] else "#222")
        if not out:
            res_title.config(text="검색 결과 (아직 없음)")
            set_text(res_text, [("아직 검색 결과가 없습니다. 오른쪽 위 '지금 검색'을 눌러주세요.\n", "n")])
            return
        r = out.get("results", {}).get(sel["key"])
        label = dict(F.SCREENER_ORDER).get(sel["key"], sel["key"])
        label = label.split(" ", 1)[1] if " " in label else label
        res_title.config(text=f"{label}  —  {out.get('date')} 기준")
        if not r:
            set_text(res_text, [("이 검색식은 꺼져 있습니다.\n", "n")]); return
        parts = [(F.SCREENER_HELP.get(sel["key"], "").split("\n", 1)[-1] + "\n", "s"), ("\n", "n")]
        if not r["signals"]:
            parts.append(("해당 종목 없음\n", "n"))
        maxn = int(cfg["output"].get("max_per_screener", 10))
        for i, sg in enumerate(r["signals"][:maxn], 1):
            parts += signal_parts(sg, i)
            parts.append(("\n", "n"))
        if r["count"] > maxn:
            parts.append((f"… 외 {r['count'] - maxn}종목 (상위 {maxn}개만 표시)\n", "s"))
        parts.append(("\n" + F.COMMON_FILTER_NOTE + "\n", "s"))
        set_text(res_text, parts)

    scanning = {"busy": False}

    def do_scan():
        if scanning["busy"]:
            return
        scanning["busy"] = True
        scan_btn.config(state="disabled")
        scan_msg.config(text="검색 중… (1~3분)")

        def work():
            try:
                run_daily_scan(cfg, progress=lambda m: root.after(0, lambda m=m: scan_msg.config(text=m[:50])))
                root.after(0, lambda: scan_msg.config(text="검색 완료"))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: scan_msg.config(text=f"검색 실패: {str(e)[:60]}"))
            finally:
                scanning["busy"] = False
                root.after(0, lambda: (scan_btn.config(state="normal"), render_results()))

        threading.Thread(target=work, daemon=True).start()

    refreshers["results"] = render_results

    # ================= 감시 종목 =================
    wpage = tk.Frame(body, bg="#f4f4f4")
    pages["watch"] = wpage
    reg(tk.Label(wpage, text="분봉 거래폭발 감시 종목", bg="#f4f4f4", fg="#222"), 4, True).pack(anchor="w")
    reg(tk.Label(wpage, text="장중에 1분 거래량이 평소의 3배 이상 터지면 알려주는 종목들입니다.", bg="#f4f4f4", fg="#555"), -2).pack(anchor="w", pady=(0, 8))
    wtop = tk.Frame(wpage, bg="#f4f4f4")
    wtop.pack(fill="x", pady=6)
    reg(tk.Label(wtop, text="종목 이름:", bg="#f4f4f4"), 0).pack(side="left")
    entry = reg(tk.Entry(wtop, width=18, bd=2, relief="solid"), 0)
    entry.pack(side="left", padx=8, ipady=6)
    add_btn = big_button(wtop, "추가", lambda: do_add(), "#1a8f3a", delta=-1)
    add_btn.pack(side="left", padx=4)
    del_btn = big_button(wtop, "선택한 종목 삭제", lambda: do_del(), "#c0392b", delta=-1)
    del_btn.pack(side="left", padx=12)
    wmsg = reg(tk.Label(wpage, text="", bg="#f4f4f4", fg="#1a5fb4", wraplength=800, justify="left"), -2)
    wmsg.pack(anchor="w", pady=(4, 6))
    lb_fr = tk.Frame(wpage, bg="white", bd=1, relief="solid")
    lb_fr.pack(fill="both", expand=True)
    wlist = reg(tk.Listbox(lb_fr, bd=0, highlightthickness=0, selectbackground="#4a90d9", activestyle="none"), 0)
    wlist.pack(side="left", fill="both", expand=True, padx=8, pady=6)
    wsb = ttk.Scrollbar(lb_fr, command=wlist.yview)
    wsb.pack(side="right", fill="y")
    wlist.configure(yscrollcommand=wsb.set)
    entry.bind("<Return>", lambda e: do_add())

    def render_watch():
        state.refresh_if_changed()
        wlist.delete(0, "end")
        for i, c in enumerate(state.watchlist, 1):
            b = state.get_baseline(c) or {}
            info = f"평소 1분 {b['mean']:,.0f}주" if b.get("mean") else "기준값 준비 중"
            wlist.insert("end", f"  {i}.  {name_of(c)}  ({c})   —   {info}")
        if not state.watchlist:
            wlist.insert("end", "  감시 종목이 없습니다. 위에 종목 이름을 넣고 '추가'를 누르세요.")

    def ensure_listing(then):
        if listing["df"] is not None:
            then(); return
        wmsg.config(text="종목 목록 받는 중…")

        def work():
            try:
                listing["df"] = fetch_listing()
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: wmsg.config(text=f"종목 목록을 받지 못했습니다: {e}")); return
            root.after(0, then)
        threading.Thread(target=work, daemon=True).start()

    def add_code(code, name):
        if not state.add_watch(code):
            wmsg.config(text=f"{name} 은 이미 감시 중입니다."); return
        entry.delete(0, "end")
        render_watch()
        wmsg.config(text=f"{name} 추가됨. 기준값(평소 1분 거래량)을 만드는 중…")

        def work():
            try:
                info = monitor.build_baseline(code, force=True)
                msg = f"{name} 준비 완료: 평소 1분 {info['mean']:,.0f}주" if info.get("mean") else f"{name}: 데이터가 없어 기준값을 못 만들었습니다"
            except Exception as e:  # noqa: BLE001
                msg = f"{name} 기준값 오류: {str(e)[:80]}"
            root.after(0, lambda: (wmsg.config(text=msg), render_watch()))
        threading.Thread(target=work, daemon=True).start()

    def choose(cands):
        win = tk.Toplevel(root)
        win.title("어느 종목인가요?")
        win.configure(bg="#f4f4f4")
        reg(tk.Label(win, text="어느 종목인가요?", bg="#f4f4f4"), 2, True).pack(pady=12, padx=20)
        for c, n in cands[:8]:
            big_button(win, f"{n}  ({c})", lambda c=c, n=n: (win.destroy(), add_code(c, n)), "#4a90d9").pack(fill="x", padx=20, pady=4)
        big_button(win, "취소", win.destroy, "#999").pack(pady=10)
        win.transient(root)
        win.grab_set()

    def do_add():
        q = entry.get().strip()
        if not q:
            wmsg.config(text="종목 이름이나 코드를 넣어주세요. 예: 삼성전자"); return

        def go():
            cands = resolve_name(q, listing["df"])
            if not cands:
                wmsg.config(text=f"'{q}' 종목을 찾지 못했습니다. 이름을 다시 확인해주세요."); return
            exact = [x for x in cands if x[1] == q]
            if len(cands) == 1 or exact:
                c, n = exact[0] if exact else cands[0]
                add_code(c, n)
            else:
                choose(cands)
        ensure_listing(go)

    def do_del():
        idx = wlist.curselection()
        if not idx or not state.watchlist:
            wmsg.config(text="목록에서 뺄 종목을 먼저 클릭하세요."); return
        i = idx[0]
        if i >= len(state.watchlist):
            return
        code = state.watchlist[i]
        name = name_of(code)
        if messagebox.askyesno("삭제", f"{name} 을(를) 감시에서 뺄까요?"):
            state.remove_watch(code)
            render_watch()
            wmsg.config(text=f"{name} 을(를) 감시에서 뺐습니다.")

    refreshers["watch"] = render_watch

    # ================= 오늘 알림 =================
    apage = tk.Frame(body, bg="#f4f4f4")
    pages["alerts"] = apage
    atitle = reg(tk.Label(apage, text="오늘 분봉 거래폭발 알림", bg="#f4f4f4", fg="#222"), 4, True)
    atitle.pack(anchor="w", pady=(0, 8))
    a_fr, a_text = make_text(apage)
    a_fr.pack(fill="both", expand=True)

    def render_alerts():
        alerts = today_alerts()
        atitle.config(text=f"오늘 분봉 거래폭발 알림  ({len(alerts)}건)")
        set_text(a_text, alert_parts(alerts))

    refreshers["alerts"] = render_alerts

    # ================= 설명 =================
    hpage = tk.Frame(body, bg="#f4f4f4")
    pages["help"] = hpage
    reg(tk.Label(hpage, text="검색식 설명", bg="#f4f4f4", fg="#222"), 4, True).pack(anchor="w", pady=(0, 8))
    h_fr, h_text = make_text(hpage)
    h_fr.pack(fill="both", expand=True)
    hparts = []
    import re as _re
    for key, txt in F.SCREENER_HELP.items():
        title, _, desc = txt.partition("\n")
        title = _re.sub(r"(\d)\ufe0f?\u20e3", r"\1.", title)  # 키캡 이모지 → 숫자 (윈도우 Tk 호환)
        hparts.append((title + "\n", "h"))
        hparts.append((desc + "\n\n", "n"))
    hparts.append((F.COMMON_FILTER_NOTE + "\n\n", "s"))
    hparts.append(("사용법\n", "h"))
    hparts.append(("• 홈: 봇 켜기/종료. '● 실행 중'이면 정상. 창은 닫지 말고 작게 두세요.\n"
                   "• 검색 결과: 위 버튼으로 검색식을 고르면 결과가 나옵니다. '지금 검색'은 1~3분 걸립니다. 장 마감 후 15:45에 자동으로도 돌아갑니다.\n"
                   "• 감시 종목: 종목 이름을 넣고 '추가'. 장중에 거래량이 터지면 홈·오늘 알림·텔레그램에 뜹니다.\n"
                   "• 글씨가 작으면 왼쪽 아래 '크게'를 누르세요.\n", "n"))
    set_text(h_text, hparts)

    # ---------- 종료 ----------
    def on_close():
        if bot.alive():
            if not messagebox.askyesno("종료", "봇을 끄고 창을 닫을까요?\n(끄면 알림이 오지 않습니다)"):
                return
            bot.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    apply_fonts()
    show(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--page=")), "home"))
    if "--no-bot" not in sys.argv:  # 테스트용: 봇을 자동으로 켜지 않음
        root.after(200, bot.start)
    if "--smoke" in sys.argv:  # 테스트용: 모든 페이지를 그려보고 내용 요약 출력 후 종료
        def smoke():
            for name in ("home", "results", "watch", "alerts", "help"):
                show(name)
                root.update()
                print(f"[{name}] ok")
            for t, label in ((res_text, "results"), (a_text, "alerts"), (h_text, "help"), (home_text, "home")):
                txt = t.get("1.0", "end").strip()
                print(f"  {label}: {len(txt)}자 / 첫줄: {txt.splitlines()[0][:70] if txt else '(빈칸)'}")
            print("  watch rows:", wlist.size(), "|", wlist.get(0)[:70])
            print("  screener buttons:", [b.cget('text') for b in sbtns.values()])
            root.destroy()
        root.after(800, smoke)
    root.after(1200, refresh_status)
    root.after(1500, refresh_home)
    root.after(3000, check_update_quietly)
    root.mainloop()


def selftest() -> int:
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
