"""텔레그램 봇 — 버튼 위주 UI, 장중 분봉 감시, 장 마감 검색 자동 발송."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from . import formatting as F
from .calendar_kr import KST, in_market_hours, is_trading_day, now_kst, parse_hhmm
from .config import kiwoom_configured
from .data.kiwoom import KiwoomClient
from .data.universe import fetch_listing, load_latest_listing, resolve_name
from .data import market
from .screeners.minute import MinuteMonitor
from .screeners.runner import load_latest_result, run_daily_scan
from . import updater
from .state import State

log = logging.getLogger("stockbot.bot")

BTN_RESULTS = "📋 오늘 검색결과"
BTN_WATCH = "🔥 감시종목"
BTN_ADD = "➕ 감시 추가"
BTN_DEL = "➖ 감시 삭제"
BTN_SCAN = "🔎 지금 검색"
BTN_HELP = "❓ 도움말"
BTN_OVERLAP = "🔗 겹침 종목"
BTN_BRIEF = "📰 오늘 시황"
# python-telegram-bot v22: run_daily 의 days 는 0=일요일 … 6=토요일 → 월~금 = (1,2,3,4,5)
WEEKDAYS = (1, 2, 3, 4, 5)
KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_RESULTS, BTN_WATCH], [BTN_OVERLAP, BTN_BRIEF], [BTN_ADD, BTN_DEL], [BTN_SCAN, BTN_HELP]],
    resize_keyboard=True, is_persistent=True,
)


class StockBot:
    def __init__(self, cfg: dict, state: State):
        self.cfg = cfg
        self.state = state
        self.started = time.time()
        self.kiwoom = None
        if kiwoom_configured(cfg):
            k = cfg["kiwoom"]
            self.kiwoom = KiwoomClient(k["app_key"], k["app_secret"], bool(k.get("is_mock", False)))
        self.listing = load_latest_listing()
        self.monitor = MinuteMonitor(cfg, state, self.kiwoom, name_of=self.name_of)
        self._scan_lock = asyncio.Lock()
        self._error_sent: dict[str, str] = {}
        self.exit_code = 0
        self._last_poll_day: dt.date | None = None

        if not state.watchlist and cfg.get("watchlist"):
            state.set_watchlist(cfg["watchlist"])
        for cid in cfg["telegram"].get("allowed_chat_ids", []):
            state.add_chat(int(cid))

        self.app = (Application.builder().token(cfg["telegram"]["bot_token"])
                    .post_init(self._post_init).build())
        self._register()

    # ───────────────────────── 유틸 ─────────────────────────
    def name_of(self, code: str) -> str:
        if self.listing is not None:
            hit = self.listing[self.listing["code"] == code]
            if len(hit):
                return str(hit["name"].iloc[0])
        return code

    def names(self, codes: list[str]) -> dict[str, str]:
        return {c: self.name_of(c) for c in codes}

    def allowed(self, chat_id: int) -> bool:
        return chat_id in self.state.chat_ids

    async def reply(self, update: Update, text: str, **kw):
        for part in F.split_message(text):
            await update.effective_message.reply_text(part, reply_markup=KEYBOARD, **kw)

    async def send_all(self, text: str, **kw):
        for cid in self.state.chat_ids:
            for part in F.split_message(text):
                try:
                    await self.app.bot.send_message(cid, part, reply_markup=KEYBOARD, **kw)
                except Exception as e:  # noqa: BLE001
                    log.warning("전송 실패 chat=%s: %s", cid, e)

    async def notify_error(self, key: str, err: Exception):
        today = now_kst().strftime("%Y-%m-%d")
        log.exception("%s 오류: %s", key, err)
        if self._error_sent.get(key) == today:
            return
        self._error_sent[key] = today
        await self.send_all(f"⚠️ {key} 중 오류가 났습니다. (오늘은 이 알림을 다시 보내지 않습니다)\n{type(err).__name__}: {str(err)[:200]}")

    async def refresh_listing(self):
        try:
            self.listing = await asyncio.to_thread(fetch_listing)
        except Exception as e:  # noqa: BLE001
            log.warning("상장목록 갱신 실패: %s", e)
            if self.listing is None:
                self.listing = load_latest_listing()

    # ───────────────────────── 핸들러 등록 ─────────────────────────
    def _register(self):
        a = self.app
        a.add_handler(CommandHandler("start", self.cmd_start))
        a.add_handler(CommandHandler("id", self.cmd_id))
        a.add_handler(CommandHandler("status", self.cmd_status))
        a.add_handler(CommandHandler("scan", self.cmd_scan))
        a.add_handler(CommandHandler("help", self.cmd_help))
        a.add_handler(CommandHandler("update", self.cmd_update))
        a.add_handler(CommandHandler("version", self.cmd_version))
        a.add_handler(CallbackQueryHandler(self.on_callback))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        a.add_error_handler(self.on_error)

        jq = a.job_queue
        sch = self.cfg["schedule"]
        jq.run_repeating(self.job_poll, interval=int(sch.get("realtime_poll_seconds", 60)), first=10, name="poll")
        jq.run_daily(self.job_prepare, time=parse_hhmm(sch.get("premarket_prepare_time", "08:30")),
                     days=WEEKDAYS, name="prepare")
        jq.run_daily(self.job_daily_scan, time=parse_hhmm(sch.get("daily_scan_time", "15:45")),
                     days=WEEKDAYS, name="daily_scan")

    async def on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE):
        err = ctx.error
        name = type(err).__name__
        if name == "Conflict":
            today = now_kst().strftime("%Y-%m-%d")
            if self._error_sent.get("conflict") != today:
                self._error_sent["conflict"] = today
                log.warning("텔레그램 중복 실행 감지 (다른 컴퓨터에서 같은 봇이 켜져 있음)")
                await self.send_all("⚠️ 같은 봇이 다른 컴퓨터에서도 켜져 있습니다. 한 곳만 남기고 꺼주세요. "
                                    "(두 곳에서 켜면 메시지가 엇갈립니다)")
            return
        log.exception("처리 중 오류: %s", err)

    async def _post_init(self, app: Application):
        if self.listing is None:
            await self.refresh_listing()
        # 시작할 때마다 감시 종목 기준값 준비 (오늘 이미 만든 건 건너뜀, 백그라운드)
        asyncio.get_running_loop().run_in_executor(None, self.monitor.prepare_all)
        log.info("봇 시작. 연결된 채팅 %s, 감시 %d종목, 키움 %s",
                 self.state.chat_ids, len(self.state.watchlist), "연결" if self.kiwoom else "미연결")

    # ───────────────────────── 명령어 ─────────────────────────
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if self.allowed(cid):
            await self.reply(update, "안녕하세요! 아래 버튼을 눌러 사용하세요.\n" + F.help_text())
        else:
            await update.effective_message.reply_text(
                "연결 번호를 보내주세요. (설정 파일 pair_code 에 적힌 숫자)")

    async def cmd_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(f"이 채팅의 ID: {update.effective_chat.id}")

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if self.allowed(update.effective_chat.id):
            await self.reply(update, F.help_text())

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.allowed(update.effective_chat.id):
            return
        up = int(time.time() - self.started)
        jobs = ", ".join(f"{j.name}@{j.next_t.astimezone(KST):%m/%d %H:%M}" for j in self.app.job_queue.jobs() if j.next_t)
        kw = "미연결(네이버 7일치 기준값)"
        if self.kiwoom:
            try:
                kw = await asyncio.to_thread(self.kiwoom.check)
            except Exception as e:  # noqa: BLE001
                kw = f"오류: {str(e)[:100]}"
        txt = (f"🤖 봇 상태 (버전 {updater.current_version()})\n가동 {up // 3600}시간 {(up % 3600) // 60}분\n"
               f"연결 채팅 {len(self.state.chat_ids)}개 · 감시 {len(self.state.watchlist)}종목\n"
               f"키움 API: {kw}\n다음 작업: {jobs}")
        await self.reply(update, txt)

    async def cmd_version(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.allowed(update.effective_chat.id):
            return
        info = await asyncio.to_thread(updater.check, self.cfg)
        txt = f"현재 버전 {info['current']}"
        if info.get("error"):
            txt += f"\n{info['error']}"
        elif info["available"]:
            txt += f"\n새 버전 {info['latest']} 이 있습니다. /update 를 보내면 업데이트합니다."
        else:
            txt += " (최신)"
        await self.reply(update, txt)

    async def cmd_update(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.allowed(update.effective_chat.id):
            return
        await self.do_update(update.effective_chat.id)

    async def do_update(self, chat_id: int):
        if self._scan_lock.locked():
            await self.app.bot.send_message(chat_id, "검색이 끝난 뒤 다시 시도해주세요.", reply_markup=KEYBOARD)
            return
        async with self._scan_lock:
            info = await asyncio.to_thread(updater.check, self.cfg)
            if info.get("error"):
                await self.app.bot.send_message(chat_id, f"⚠️ 업데이트 확인 실패\n{info['error']}", reply_markup=KEYBOARD)
                return
            if not info["available"]:
                await self.app.bot.send_message(chat_id, f"이미 최신 버전입니다 ({info['current']}).", reply_markup=KEYBOARD)
                return
            await self.app.bot.send_message(chat_id, f"⬇️ {info['current']} → {info['latest']} 업데이트 중… 잠시 후 봇이 자동으로 다시 켜집니다.",
                                            reply_markup=KEYBOARD)
            res = await asyncio.to_thread(updater.apply, self.cfg)
            if not res.get("updated"):
                await self.app.bot.send_message(chat_id, f"⚠️ 업데이트 실패: {res.get('error')}", reply_markup=KEYBOARD)
                return
            notes = "\n".join("• " + n for n in res.get("notes", [])[:6])
            txt = f"✅ 업데이트 완료: {res['from']} → {res['to']}"
            if res.get("pip"):
                txt += f"\n{res['pip']}"
            if notes:
                txt += "\n\n바뀐 점\n" + notes
            txt += "\n\n다시 켜는 중… 1분 뒤 /status 로 확인하세요."
            await self.send_all(txt)
            self.exit_code = 10
            self.app.stop_running()

    async def cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if self.allowed(update.effective_chat.id):
            await self.do_scan(update)

    # ───────────────────────── 텍스트 라우팅 ─────────────────────────
    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self.state.refresh_if_changed()
        cid = update.effective_chat.id
        text = (update.effective_message.text or "").strip()

        if not self.allowed(cid):
            if text == str(self.cfg["telegram"].get("pair_code", "")):
                self.state.add_chat(cid)
                await self.reply(update, "✅ 연결되었습니다! 이제 알림을 보내드립니다.\n\n" + F.help_text())
            else:
                await update.effective_message.reply_text("연결 번호를 보내주세요. (설정 파일 pair_code 에 적힌 숫자)")
            return

        pending = self.state.get_pending(cid)
        if text == BTN_HELP:
            self.state.set_pending(cid, None)
            await self.reply(update, F.help_text())
        elif text in ("업데이트", "/update", "update"):
            self.state.set_pending(cid, None)
            await self.do_update(cid)
        elif text == BTN_RESULTS:
            self.state.set_pending(cid, None)
            await self.screener_menu(update, "res")
        elif text == BTN_WATCH:
            self.state.set_pending(cid, None)
            await self.show_watchlist(update)
        elif text == BTN_ADD:
            self.state.set_pending(cid, "add")
            await self.reply(update, "감시에 추가할 종목 이름이나 코드를 보내주세요.\n예: 삼성전자 / 005930")
        elif text == BTN_DEL:
            if not self.state.watchlist:
                await self.reply(update, "감시 종목이 없습니다.")
                return
            self.state.set_pending(cid, "del")
            await self.show_watchlist(update, prefix="삭제할 종목 이름이나 번호를 보내주세요.\n\n")
        elif text == BTN_SCAN:
            self.state.set_pending(cid, None)
            await self.screener_menu(update, "scan")
        elif text == BTN_OVERLAP:
            self.state.set_pending(cid, None)
            out = load_latest_result()
            if not out:
                await self.reply(update, "아직 검색 결과가 없습니다. '🔎 지금 검색'을 눌러주세요.")
            else:
                for m in F.format_overlap(out):
                    await self.reply(update, m)
        elif text == BTN_BRIEF:
            self.state.set_pending(cid, None)
            await self.reply(update, "시황을 가져오는 중…")
            try:
                b = await asyncio.to_thread(market.morning_brief)
                await self.reply(update, market.format_brief(b))
            except Exception as e:  # noqa: BLE001
                await self.reply(update, f"시황 조회 실패: {str(e)[:100]}")
        elif pending == "add":
            await self.handle_add(update, text)
        elif pending == "del":
            await self.handle_del(update, text)
        else:
            m = re.match(r"^(.+?)\s*(추가|삭제|빼|제거)\s*$", text)
            if m:
                q, act = m.group(1), m.group(2)
                if act == "추가":
                    await self.handle_add(update, q)
                else:
                    await self.handle_del(update, q)
            else:
                await self.reply(update, "아래 버튼을 눌러 사용하세요. (종목 이름 뒤에 '추가'/'삭제'를 붙여 보내도 됩니다)")

    # ───────────────────────── 감시 종목 관리 ─────────────────────────
    async def handle_add(self, update: Update, query: str):
        cid = update.effective_chat.id
        if self.listing is None:
            await self.refresh_listing()
        cands = resolve_name(query, self.listing) if self.listing is not None else []
        if not cands:
            await self.reply(update, f"'{query}' 종목을 찾지 못했습니다. 다시 보내주세요.")
            return
        if len(cands) > 1 and not any(n == query for _, n in cands):
            kb = [[InlineKeyboardButton(f"{n} ({c})", callback_data=f"add:{c}")] for c, n in cands[:8]]
            await update.effective_message.reply_text("어느 종목인가요?", reply_markup=InlineKeyboardMarkup(kb))
            return
        code, name = next(((c, n) for c, n in cands if n == query), cands[0])
        self.state.set_pending(cid, None)
        await self.add_watch(update.effective_chat.id, code, name)

    async def add_watch(self, chat_id: int, code: str, name: str):
        if not self.state.add_watch(code):
            await self.app.bot.send_message(chat_id, f"{name} ({code}) 은 이미 감시 중입니다.", reply_markup=KEYBOARD)
            return
        await self.app.bot.send_message(chat_id, f"✅ {name} ({code}) 감시 추가. 기준값을 만드는 중…", reply_markup=KEYBOARD)

        async def build():
            try:
                info = await asyncio.to_thread(self.monitor.build_baseline, code, True)
                if info.get("mean"):
                    txt = f"📐 {name} 준비 완료: 평소 1분 거래량 {info['mean']:,.0f}주"
                else:
                    txt = f"⚠️ {name} 기준값을 만들지 못했습니다 (데이터 없음). 내일 아침 다시 시도합니다."
            except Exception as e:  # noqa: BLE001
                txt = f"⚠️ {name} 기준값 생성 오류: {str(e)[:150]}"
            await self.app.bot.send_message(chat_id, txt, reply_markup=KEYBOARD)

        asyncio.create_task(build())

    async def handle_del(self, update: Update, query: str):
        cid = update.effective_chat.id
        wl = self.state.watchlist
        code = None
        if query.isdigit() and len(query) <= 3 and 1 <= int(query) <= len(wl):
            code = wl[int(query) - 1]
        elif re.fullmatch(r"\d{6}", query) and query in wl:
            code = query
        else:
            q = query.replace(" ", "").upper()
            hits = [c for c in wl if q and q in self.name_of(c).replace(" ", "").upper()]
            if len(hits) == 1:
                code = hits[0]
            elif len(hits) > 1:
                kb = [[InlineKeyboardButton(f"{self.name_of(c)} ({c})", callback_data=f"del:{c}")] for c in hits]
                await update.effective_message.reply_text("어느 종목을 뺄까요?", reply_markup=InlineKeyboardMarkup(kb))
                return
        if code is None:
            await self.reply(update, f"감시 목록에서 '{query}' 를 찾지 못했습니다.")
            return
        self.state.set_pending(cid, None)
        name = self.name_of(code)
        self.state.remove_watch(code)
        await self.reply(update, f"🗑 {name} ({code}) 감시에서 뺐습니다.")

    async def on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        cid = q.message.chat.id
        if not self.allowed(cid):
            return
        act, _, code = (q.data or "").partition(":")
        self.state.set_pending(cid, None)
        if act in ("scan", "res"):
            try:
                await q.edit_message_text(f"{'전체' if code == 'all' else F.screener_label(code)} 선택")
            except Exception:  # noqa: BLE001
                pass
            await self.send_screener(cid, code, fresh=(act == "scan"))
            return
        name = self.name_of(code)
        if act == "add":
            await q.edit_message_text(f"{name} ({code}) 선택")
            await self.add_watch(cid, code, name)
        elif act == "del":
            self.state.remove_watch(code)
            await q.edit_message_text(f"🗑 {name} ({code}) 감시에서 뺐습니다.")

    async def show_watchlist(self, update: Update, prefix: str = ""):
        wl = self.state.watchlist
        txt = F.format_watchlist(wl, self.names(wl), self.state.data.get("baselines", {}))
        await self.reply(update, prefix + txt)
        # 기준값이 없는 종목은 지금 바로 만든다
        missing = [c for c in wl if not (self.state.get_baseline(c) or {}).get("mean")]
        if missing:
            chat_id = update.effective_chat.id
            names = ", ".join(self.name_of(c) for c in missing)
            await self.app.bot.send_message(chat_id, f"📐 {names} 기준값을 지금 만드는 중… 잠시 후 알려드립니다.", reply_markup=KEYBOARD)

            async def build():
                done = []
                for c in missing:
                    try:
                        info = await asyncio.to_thread(self.monitor.build_baseline, c, True)
                        if info.get("mean"):
                            done.append(f"{self.name_of(c)}: 평소 1분 {info['mean']:,.0f}주")
                        else:
                            done.append(f"{self.name_of(c)}: 데이터 없음")
                    except Exception as e:  # noqa: BLE001
                        done.append(f"{self.name_of(c)}: 오류 {str(e)[:80]}")
                await self.app.bot.send_message(chat_id, "📐 기준값 준비 완료\n" + "\n".join(done), reply_markup=KEYBOARD)

            asyncio.create_task(build())

    # ───────────────────────── 검색 실행/결과 ─────────────────────────
    async def screener_menu(self, update: Update, mode: str):
        """검색식 선택 인라인 버튼. mode='res' 저장된 결과 보기, 'scan' 지금 실행."""
        out = load_latest_result() if mode == "res" else None
        if mode == "res" and not out:
            await self.reply(update, "아직 검색 결과가 없습니다. '🔎 지금 검색'을 눌러주세요.")
            return
        rows = []
        for key, label in F.SCREENER_ORDER:
            if not self.cfg["screeners"].get(key, {}).get("enabled", True):
                continue
            cnt = ""
            if out is not None:
                r = out.get("results", {}).get(key)
                cnt = f" ({r['count']})" if r else ""
            rows.append([InlineKeyboardButton(label + cnt, callback_data=f"{mode}:{key}")])
        if mode == "res" and out is not None and out.get("overlap"):
            rows.append([InlineKeyboardButton(f"🔗 겹침 종목 ({len(out['overlap'])})", callback_data="res:overlap")])
        rows.append([InlineKeyboardButton("📚 전체 한 번에", callback_data=f"{mode}:all")])
        title = (f"📋 {out.get('date')} 기준 결과입니다. 볼 검색식을 고르세요." if mode == "res"
                 else "🔎 지금 실행할 검색식을 고르세요. (1~3분 걸릴 수 있음)")
        await update.effective_message.reply_text(title, reply_markup=InlineKeyboardMarkup(rows))

    async def latest_or_run(self, chat_id: int, fresh: bool) -> dict | None:
        """fresh=True 면 10분 이내 결과는 재사용, 아니면 새로 실행."""
        out = load_latest_result()
        if out and fresh:
            try:
                ran = dt.datetime.strptime(out.get("run_at", ""), "%Y-%m-%d %H:%M")
                if (dt.datetime.now() - ran) <= dt.timedelta(minutes=10):
                    return out
            except ValueError:
                pass
        if not fresh:
            return out
        if self._scan_lock.locked():
            await self.app.bot.send_message(chat_id, "이미 검색 중입니다. 잠시만요…", reply_markup=KEYBOARD)
            return None
        async with self._scan_lock:
            await self.app.bot.send_message(chat_id, "🔎 검색 중… 잠시만 기다려주세요.", reply_markup=KEYBOARD)
            try:
                return await asyncio.to_thread(run_daily_scan, self.cfg)
            except Exception as e:  # noqa: BLE001
                await self.notify_error("검색 실행", e)
                return None

    async def send_screener(self, chat_id: int, key: str, fresh: bool):
        out = await self.latest_or_run(chat_id, fresh)
        if not out:
            if not fresh:
                await self.app.bot.send_message(chat_id, "아직 검색 결과가 없습니다. '🔎 지금 검색'을 눌러주세요.", reply_markup=KEYBOARD)
            return
        note = ""
        if in_market_hours(self.cfg):
            note = "⏳ 장중 기준: 오늘 거래량·종가는 아직 진행 중인 값입니다."
        max_per = int(self.cfg["output"].get("max_per_screener", 30))
        rd = int(self.cfg["output"].get("repeat_days", 20))
        msgs = (F.format_daily_results(out, max_per, note) if key == "all"
                else F.format_overlap(out) if key == "overlap"
                else F.format_one_screener(out, key, max_per, note, rd))
        for m in msgs:
            await self.app.bot.send_message(chat_id, m, reply_markup=KEYBOARD)
        if key == "all":
            await self.send_minute_summary(chat_id, out.get("date", ""))

    async def show_results(self, update: Update):
        await self.send_screener(update.effective_chat.id, "all", fresh=False)

    async def send_minute_summary(self, chat_id: int, date: str):
        alerts = self.state.minute_alerts(date)
        if not alerts:
            return
        lines = [f"🔥 {date} 분봉 거래폭발 알림 {len(alerts)}건"]
        for a in alerts[-10:]:
            lines.append(f"{a['time']} {a['name']} {a['multiple']}배 ({a['change_pct']:+.1f}%)")
        for part in F.split_message("\n".join(lines)):
            await self.app.bot.send_message(chat_id, part, reply_markup=KEYBOARD)

    async def do_scan(self, update: Update | None = None, auto: bool = False):
        if self._scan_lock.locked():
            if update:
                await self.reply(update, "이미 검색 중입니다. 잠시만요…")
            return
        async with self._scan_lock:
            if update:
                await self.reply(update, "🔎 검색을 시작합니다. 1~3분 정도 걸립니다…")
                await update.effective_chat.send_action(ChatAction.TYPING)
            try:
                out = await asyncio.to_thread(run_daily_scan, self.cfg)
            except Exception as e:  # noqa: BLE001
                await self.notify_error("검색 실행", e)
                return
            today = now_kst().strftime("%Y-%m-%d")
            if auto and out.get("date") != today:
                await self.send_all(f"오늘({today})은 휴장일로 보입니다. 최근 거래일 {out.get('date')} 결과는 이미 보냈으니 생략합니다.")
                return
            note = ""
            if in_market_hours(self.cfg):
                note = "⏳ 장중 실행: 오늘 거래량·종가는 아직 진행 중인 값입니다."
            elif out.get("date") != today:
                note = f"(최근 거래일 {out.get('date')} 기준)"
            msgs = F.format_daily_results(out, int(self.cfg["output"].get("max_per_screener", 30)), note)
            if update and not auto:
                for m in msgs:
                    await self.reply(update, m)
            else:
                for m in msgs:
                    await self.send_all(m)
                for cid in self.state.chat_ids:
                    await self.send_minute_summary(cid, today)

    # ───────────────────────── 예약 작업 ─────────────────────────
    async def job_poll(self, ctx: ContextTypes.DEFAULT_TYPE):
        now = now_kst()
        if self._last_poll_day != now.date():
            self._last_poll_day = now.date()
            self.monitor.reset_day()
        if self.state.refresh_if_changed():
            missing = [c for c in self.state.watchlist if not (self.state.get_baseline(c) or {}).get("mean")]
            for c in missing:
                asyncio.get_running_loop().run_in_executor(None, self.monitor.build_baseline, c)
        if not is_trading_day(self.cfg, now.date()) or not in_market_hours(self.cfg, now):
            return
        if not self.state.watchlist or not self.state.chat_ids:
            return
        try:
            alerts = await asyncio.to_thread(self.monitor.poll, now)
        except Exception as e:  # noqa: BLE001
            await self.notify_error("분봉 감시", e)
            return
        for a in alerts:
            await self.send_all(F.format_minute_alert(a))

    async def job_prepare(self, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_trading_day(self.cfg):
            return
        await self.refresh_listing()
        try:
            info = await asyncio.to_thread(self.monitor.prepare_all, True)
        except Exception as e:  # noqa: BLE001
            await self.notify_error("기준값 준비", e)
            return
        if not self.state.watchlist:
            return
        ok = [c for c, i in info.items() if i.get("mean")]
        bad = [self.name_of(c) for c in self.state.watchlist if c not in ok]
        txt = f"🌅 좋은 아침입니다. 오늘 분봉 감시 준비 완료: {len(ok)}종목 (버전 {updater.current_version()})"
        if bad:
            txt += f"\n⚠️ 기준값 없음: {', '.join(bad)}"
        await self.send_all(txt)
        try:
            b = await asyncio.to_thread(market.morning_brief)
            await self.send_all(market.format_brief(b))
        except Exception as e:  # noqa: BLE001
            log.warning("아침 시황 실패: %s", e)

    async def job_daily_scan(self, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_trading_day(self.cfg):
            return
        await self.do_scan(None, auto=True)

    # ───────────────────────── 실행 ─────────────────────────
    def run(self):
        self.app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
