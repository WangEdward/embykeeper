from __future__ import annotations

import asyncio
import re
import secrets
import string
import threading
import uuid
from datetime import datetime, timedelta
from enum import EnumMeta, IntEnum
from functools import wraps
from pathlib import Path

from appdirs import user_config_dir
from loguru import logger
from teleclient.client import AuthorizationState
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler, filters)
from tinydb import TinyDB, where

from ..telechecker.main import login
from .paginator import Paginator, SelectPaginator


def confirm_key(user_data, info, callback_ok, callback_cancel="delete"):
    key = str(uuid.uuid4())
    user_data[key] = (info, callback_ok, callback_cancel)
    return key


def command(require=TelegramUserRole.USER):
    def deco(func):
        @wraps(func)
        async def wrapper(self: TelegramBot, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kw):
            if update.message is not None:
                user = update.message.from_user
                messager = update.message.reply_text
            elif update.callback_query is not None:
                query = update.callback_query
                await query.answer()
                user = update.callback_query.from_user
                messager = lambda t: context.bot.send_message(chat_id=update.effective_chat.id, text=t)
            if self.has_perm(user.id, require=require):
                try:
                    return await func(self, update, context, *args, **kw)
                except Exception as e:
                    await messager("⚠️ 发生错误.")
                    # logger.warning(f'Telegram bot 发生错误: "{e}".')
                    raise e from None
            else:
                await messager("⚠️ 没有权限.")

        return wrapper

    return deco


class TelegramBot:
    def __init__(self, token, config={}, proxy_host=None, proxy_port=None, proxy_type=None):
        self.config = config
        data_dir = Path(user_config_dir("embykeeper"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = TinyDB(data_dir / "telegram_notifier.db")
        self.users = self.db.table("users")
        self.tokens = self.db.table("tokens")
        self.telegrams = self.db.table("telegrams")
        self.embys = self.db.table("embys")
        self.lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        if all((proxy_type, proxy_host, proxy_port)):
            proxy = f"{proxy_type}://{proxy_host}:{proxy_port}"
            self.app = ApplicationBuilder().token(token).proxy_url(proxy).get_updates_proxy_url(proxy).build()
        else:
            self.app = ApplicationBuilder().token(token).build()
        self.app.job_queue.run_once(self.initialize, 1, job_kwargs={"misfire_grace_time": None})
        self.app.add_handlers(
            [
                CommandHandler("start", self.start),
                CommandHandler("invite", self.invite_token),
                CommandHandler("remove", self.remove_token),
                CommandHandler("users", self.list_users),
                CommandHandler("admins", self.list_admins),
                CommandHandler("kick", self.kick),
                CommandHandler("manage", self.manage),
                ConversationHandler(
                    entry_points=[CommandHandler("telegram", self.add_telegram)],
                    states={
                        1: [MessageHandler(filters.TEXT & (~filters.COMMAND), self._add_telegram_phone)],
                        2: [MessageHandler(filters.TEXT & (~filters.COMMAND), self._add_telegram_api_id)],
                        3: [MessageHandler(filters.TEXT & (~filters.COMMAND), self._add_telegram_api_hash)],
                        4: [MessageHandler(filters.TEXT & (~filters.COMMAND), self._add_telegram_code)],
                    },
                    fallbacks=[CommandHandler("cancel", self.cancel)],
                ),
                CallbackQueryHandler(self._confirm, pattern="^confirm:"),
                CallbackQueryHandler(self._delete, pattern="^delete$"),
                CallbackQueryHandler(self._manage_users, pattern="^manage#users$"),
                CallbackQueryHandler(self._manage_users_toggle_kick, pattern="^manage#users#toggleKick:"),
                CallbackQueryHandler(self._manage_users_toggle_admin, pattern="^manage#users#toggleAdmin:"),
            ]
        )

    def start_daemon(self):
        async def daemon():
            async with self.app:
                await self.app.start()
                await self.app.updater.start_polling()
                await self.stop_event.wait()
                await self.app.updater.stop()
                await self.app.stop()

        t = threading.Thread(target=asyncio.run, args=(daemon(),))
        t.daemon = True
        t.start()

    def stop_daemon(self):
        self.stop_event.set()

    async def initialize(self, context: ContextTypes.DEFAULT_TYPE):
        n_users = len(self.users.all())
        n_tokens = len(self.tokens.search(where("expire") > datetime.now().timestamp()))
        if not n_users:
            token = self.gen_token(role=TelegramUserRole.CREATOR)
            logger.info(f'初始化 Telegram Bot, 请向 "@{context.bot.username}" 发送 "/start {token}" 以绑定管理员.')
        else:
            logger.info(f"启动 Telegram Bot, 目前有 {n_users} 个用户,  {n_tokens} 个有效邀请.")
        await context.bot.set_my_commands(
            [
                ("start", "认证用户: /start <token>"),
                ("telegram", "绑定Telegram"),
                ("admins", "查看所有管理员"),
                ("users", "查看所有用户"),
                ("manage", "进入管理界面"),
                ("invite", "生成邀请token"),
                ("remove", "删除邀请token"),
                ("kick", "删除用户"),
            ]
        )

    def get_users(self, roles=(), field=None):
        if not roles:
            users = self.users.all()
        else:
            users = self.users.search(where("role").any(roles))
        if not field:
            yield from users
        elif isinstance(field, str):
            for u in users:
                yield u.get(field, None)
        else:
            for u in users:
                yield dict((k, u[k]) for k in field if k in u)

    def get_perm(self, id):
        if id == "@creator":
            user = self.users.get(where("role") == TelegramUserRole.CREATOR)
        else:
            user = self.users.get(where("id") == int(id))
        if user:
            return TelegramUserRole(user["role"])
        else:
            return None

    def has_perm(self, id, require=TelegramUserRole.ADMIN):
        if not require:
            return True
        perm = self.get_perm(id)
        if perm:
            return perm >= require
        else:
            return False

    def gen_token(self, role=TelegramUserRole.USER, times=1, days=1):
        token = "".join(secrets.choice(string.digits) for _ in range(6))
        expire = (datetime.today() + timedelta(days=days)).timestamp()
        for _ in range(times):
            self.tokens.insert({"token": token, "expire": expire, "role": role})
        return token

    @command(TelegramUserRole.ADMIN)
    async def invite_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.args:
            times = int(context.args[0])
        else:
            times = 1
        lines = [f'🔑 已生成可使用{times}次的token码 "{self.gen_token(times=times)}".', f"🕒 有效时间 1 天."]
        await update.message.reply_text("\n".join(lines))

    @command(require=None)
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lines = ["🎞️ 欢迎使用Emby保活机器人!"]
        role = self.get_perm(update.message.from_user.id)
        if role:
            if role >= TelegramUserRole.ADMIN:
                spec = "👑 管理员"
            elif role >= TelegramUserRole.USER:
                spec = " "
            else:
                spec = "🚫 封禁用户"
            lines.append(f"{spec} {update.message.from_user.full_name}, 有什么可以帮您?")
        elif not context.args:
            lines.append('ℹ️ 请使用"/start <token>"以进行认证.')
        else:
            async with self.lock:
                for s in self.tokens.search(where("token") == context.args[0]):
                    expire = s.get("expire", None)
                    if (not expire) or datetime.fromtimestamp(expire) > datetime.now():
                        self.tokens.remove(doc_ids=[s.doc_id])
                        if s["role"] == TelegramUserRole.CREATOR:
                            if len(self.users.search(where("role") == TelegramUserRole.CREATOR)) > 0:
                                lines.append("⚠️ 认证失败: 无法添加多个creator.")
                                return
                        id = update.message.from_user.id
                        name = update.message.from_user.username
                        name = f"@{name}" if name else update.message.from_user.name
                        self.users.insert(
                            {"name": name, "id": id, "role": s["role"], "timestamp": datetime.now().timestamp()}
                        )
                        lines.append(f"🥰 认证成功, 您的id为: {id}.")
                        if s["role"] == TelegramUserRole.CREATOR:
                            lines.append("👑 您也可以用: @creator")
                        break
                else:
                    lines.append("😿 认证失败.")
        await update.message.reply_text("\n\n".join(lines))

    @command(TelegramUserRole.ADMIN)
    async def remove_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.args:
            token = context.args[0]
            self.tokens.remove(where("token") == token)
            await update.message.reply_text("✅ 成功清除该token.")
        else:
            self.tokens.truncate()
            await update.message.reply_text("✅ 成功清除所有token.")

    @command(TelegramUserRole.ADMIN)
    async def list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lines = []
        for u in self.users.search(where("role") == TelegramUserRole.USER):
            lines.append(f'{u["name"]} ({u["id"]})')
        pager = Paginator("user", lines=lines, header="用户:")
        pager.register_handler(self.app)
        await pager.send_page(update, context)

    @command(TelegramUserRole.USER)
    async def list_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lines = ["管理员:"]
        for u in self.users.search(where("role") >= TelegramUserRole.ADMIN):
            lines.append(f'{u["name"]} ({u["id"]})')
        await update.message.reply_text("\n".join(lines))

    @command(TelegramUserRole.ADMIN)
    async def kick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.args:
            lines = []
            for query in context.args:
                try:
                    id = int(query)
                except:
                    id = self.users.search(where("role").matches(query, flags=re.IGNORECASE))
                    if len(id) > 1:
                        lines.append(f"⚠️ 多个该名称用户: {query}.")
                        break
                    id = int(id[0].get("id"))
                self_id = int(update.message.from_user.id)
                perm_id = self.get_perm(id)
                perm_self_id = self.get_perm(self_id)
                if perm_id is None:
                    lines.append(f"⚠️ 不存在该用户: {id}.")
                elif id == self_id:
                    lines.append(f"⚠️ 无法踢出自己: {id}.")
                elif perm_id >= perm_self_id:
                    lines.append(f"⚠️ 没有权限踢出: {id}.")
                else:
                    self.users.update({"role": TelegramUserRole.BLOCKED}, where("id") == id)
                    lines.append(f"✅ 成功踢出用户: {id}.")
            await update.message.reply_text("\n".join(lines))
        else:
            return await self.manage(update, context)

    @command(TelegramUserRole.ADMIN)
    async def manage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("用户", callback_data="manage#users"),
                    InlineKeyboardButton("状态", callback_data="manage#status"),
                ]
            ]
        )
        await update.message.reply_text("👑 请选择您要管理的内容:", reply_markup=markup)

    @command(TelegramUserRole.ADMIN)
    async def _manage_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        items = {f"{u['name']} ({u['id']})": u["id"] for u in self.users.all()}
        pager = SelectPaginator(
            "userselect", items=items, header="🔍 所有用户:\n", select_callback=self._manage_users_select_callback
        )
        pager.register_handler(self.app)
        await pager.update_page(update, context, page=1)

    @command(TelegramUserRole.ADMIN)
    async def _manage_users_select_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, item=None):
        user = self.users.get(where("id") == item)
        info = "\n".join(
            [
                "🔍 用户详情:",
                f"ID: {user['id']}",
                f"名称: {user['name']}",
                f"权限: {TelegramUserRole(user['role']).name}",
                f"加入日期: {datetime.fromtimestamp(user['timestamp']).strftime('%Y-%m-%d')}",
            ]
        )
        cmds = {
            "toggleKick": "取消踢出" if user["role"] == TelegramUserRole.BLOCKED else "立刻踢出",
            "toggleAdmin": "卸任管理" if user["role"] >= TelegramUserRole.ADMIN else "升为管理",
        }
        prompt = lambda cmd: f"将 {user['id']} ({user['name']}) {cmd}"
        keys = {}
        for cb, cmd in cmds.items():
            keys[cb] = confirm_key(context.user_data, prompt(cmd), f'manage#users#{cb}:{user["id"]}', "manage#users")
        buttons = [InlineKeyboardButton(cmds[cb], callback_data=f"confirm:{keys[cb]}") for cb in cmds]
        markup = InlineKeyboardMarkup([buttons, [InlineKeyboardButton("返回", callback_data="manage#users")]])
        await update.callback_query.edit_message_text(info, reply_markup=markup)

    @command(TelegramUserRole.ADMIN)
    async def _manage_users_toggle_kick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        id = int(query.data.split(":")[-1])
        user = self.users.get(where("id") == id)
        self_id = int(query.from_user.id)
        perm_id = self.get_perm(id)
        perm_self_id = self.get_perm(self_id)
        if id == self_id:
            return await query.edit_message_text(f"⚠️ 无法调整自己: {id}.")
        elif perm_id >= perm_self_id:
            return await query.edit_message_text(f"⚠️ 没有权限调整: {id}.")
        if perm_id == TelegramUserRole.BLOCKED:
            target = TelegramUserRole.USER
        else:
            target = TelegramUserRole.BLOCKED
        self.users.update({"role": target}, doc_ids=[user.doc_id])
        return await query.edit_message_text(f"✅ 成功调整用户: {id}.")

    @command(TelegramUserRole.CREATOR)
    async def _manage_users_toggle_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        id = int(query.data.split(":")[-1])
        user = self.users.get(where("id") == id)
        perm_id = self.get_perm(id)
        if perm_id == TelegramUserRole.ADMIN:
            target = TelegramUserRole.USER
        elif perm_id == TelegramUserRole.USER:
            target = TelegramUserRole.ADMIN
        else:
            return await query.edit_message_text(f"⚠️ 无法用于权限为{perm_id.name}的用户.")
        self.users.update({"role": target}, doc_ids=[user.doc_id])
        return await query.edit_message_text(f"✅ 成功调整用户: {id}.")

    @command(None)
    async def _confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        key = query.data.split(":")[-1]
        info, callback_ok, callback_cancel = context.user_data.pop(key)
        info = f"🚨 你确定要{info}吗?"
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("确定", callback_data=callback_ok),
                    InlineKeyboardButton("返回", callback_data=callback_cancel),
                ]
            ]
        )
        await query.edit_message_text(info, reply_markup=markup)

    @command(None)
    async def _delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.delete_message()

    @command(None)
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.pop("conv", None)

    @command(TelegramUserRole.USER)
    async def add_telegram(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["conv"] = {}
        info = "\n".join(
            ["ℹ️ 您需要输入您的Telegram API信息以使用该Bot:", "ℹ️ 您可以从下方按钮获取", "", '➡️ 请输入您的注册手机号 (类似 "+8613800000000"):']
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("Telegram 官网", url="https://my.telegram.org/")]])
        await update.message.reply_text(info, reply_markup=markup)
        return 1

    @command(TelegramUserRole.USER)
    async def _add_telegram_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["conv"]["phone"] = update.message.text
        await update.message.reply_text("➡️ 请输入您的 api_id:")
        return 2

    @command(TelegramUserRole.USER)
    async def _add_telegram_app_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["conv"]["app_id"] = update.message.text
        await update.message.reply_text("➡️ 请输入您的 api_hash:")
        
        return 3

    @command(TelegramUserRole.USER)
    async def _add_telegram_app_hash(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.telegrams.insert(update.message.from_user.id)
        context.user_data["conv"]["app_hash"] = update.message.text
        await update.message.reply_text("➡️ 由于您的设置, 我们还需要您的验证码以登录, 请在手机客户端上查看并输入:")
        return ConversationHandler.END
