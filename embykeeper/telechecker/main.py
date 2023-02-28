import time
from threading import Event

import click
from appdirs import user_cache_dir
from loguru import logger
from teleclient.client import AuthorizationState, Telegram

from . import *

CHECKINERS = (JMSCheckin, TerminusCheckin, JMSIPTVCheckin, LJYYCheckin, PeachCheckin)


def _default_login_callback(config, tg, account):
    state = tg.login(blocking=False)
    while not state == AuthorizationState.READY:
        if config.get("quiet", False) == True:
            logger.error(f'账号 "{account["phone"]}" 需要额外的信息以登录, 但由于quiet模式而跳过.')
            continue
        if state == AuthorizationState.WAIT_CODE:
            tg.send_code(click.prompt(f'请在客户端接收验证码 ({account["phone"]})', type=str))
        if state == AuthorizationState.WAIT_PASSWORD:
            tg.send_password(click.prompt(f'请输入密码 ({account["phone"]})', type=str, hide_input=True))
        state = tg.login(blocking=False)


def login(config, login_callback=_default_login_callback):
    proxy = config.get("proxy", None)
    if proxy:
        proxy_host = proxy.get("host", "127.0.0.1")
        proxy_port = proxy.get("port", "1080")
        proxy_type = proxy.get("type", "socks5")
        if proxy_type.lower() == "socks5":
            proxy_type = {"@type": "proxyTypeSocks5"}
        elif proxy_type.lower() in ("http", "https"):
            proxy_type = {"@type": "proxyTypeHttp"}
        else:
            raise ValueError(f'proxy_type "{proxy_type}" is not supported.')
    else:
        proxy_host = proxy_port = proxy_type = None
    for a in config.get("telegram", ()):
        logger.info(f'登录 Telegram: {a["phone"]}.')
        up = a.get("useproxy", True)
        tg = Telegram(
            tdlib_verbosity=1,
            api_id=a["api_id"],
            api_hash=a["api_hash"],
            phone=a["phone"],
            database_encryption_key="passw0rd!",
            files_directory=user_cache_dir("telegram"),
            proxy_server=proxy_host if up else None,
            proxy_port=proxy_port if up else None,
            proxy_type=proxy_type if up else None,
        )
        login_callback(config, tg, a)
        me = tg.get_me()
        me.wait()
        if me.error:
            logger.error(f'账号 "{tg.phone}" 无法读取用户名而跳过.')
            continue
        else:
            tg.username = f"{me.update['first_name']} {me.update['last_name']}"
            logger.info(f"欢迎你: {tg.username}.")
        chats = tg.get_chats()
        chats.wait()
        if chats.error:
            logger.error(f'账号 "{tg.username}" 无法读取会话而跳过.')
            continue
        yield tg


def _parse_update(tg, update, cache={}):
    if "text" in update["message"]["content"]:
        text = update["message"]["content"]["text"]["text"]
        text = text.replace("\n", " ")
        sender_id = update["message"]["sender_id"]["user_id"]
        if update["message"]["is_outgoing"]:
            sender_name = "Me"
        else:
            sender_name = cache.get(sender_id, None)
            if not sender_name:
                sender = tg.get_user(sender_id)
                sender.wait()
                if sender.error:
                    sender_name = f"<Unknown User {sender_id}>"
                else:
                    sender_name = f"{sender.update['first_name']} {sender.update['last_name']}"
                cache[sender_id] = sender_name
        return "{} > {}: {} (chatid = {}, userid = {}) ".format(
            tg.username,
            sender_name.strip(),
            (text[:50] + "...") if len(text) > 50 else text,
            update["message"]["chat_id"],
            sender_id,
        )


def main(config, follow=False, **kw):
    if not follow:
        for tg in login(config, **kw):
            checkiners = [cls(tg, config.get("retries", 10)) for cls in CHECKINERS]
            for c in checkiners:
                logger.info(c.msg("开始执行签到."))
                c.checkin()
            endtime = time.time() + config.get("timeout", 120)
            for c in checkiners:
                timeout = endtime - time.time()
                if timeout:
                    if not c.finished.wait(timeout):
                        logger.error(c.msg("无法在时限内完成签到."))
                else:
                    if not c.finished.is_set():
                        logger.error(c.msg("无法在时限内完成签到."))
            logger.info("运行完成.")
    else:
        for tg in login(config, **kw):
            logger.info(f"等待新消息更新以获取 ChatID.")
            cache = {}

            def message_dumper(update):
                line = _parse_update(tg, update, cache)
                if line:
                    print(line)

            tg.add_update_handler("updateNewMessage", message_dumper)
        Event().wait()
