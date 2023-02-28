import json
import time
from logging import Handler
from pathlib import Path
from queue import Queue
from threading import Thread

import requests
from appdirs import user_data_dir
from dateutil import parser
from loguru import logger
from schedule import Scheduler
from telegram.ext import Application, ContextTypes
from tinydb import Query, TinyDB


def prefix(levelno):
    line = "📋Emby签到: "
    if levelno >= 40:
        line += "⚠️"
    return line


class NotifierHandler(Handler):
    def __init__(self, time=None) -> None:
        self.cache = Queue()
        self.scheduler = Scheduler()

        if time:
            time = parser.parse(time).time().strftime("%H:%M")
            self.scheduler.every().day.at(time).do(self.consume)
            self.start_watcher()
        else:
            self.emit = self.send

    def emit(self, record):
        self.cache.put(record)

    def send(self, record):
        raise NotImplementedError()

    def start_watcher(self):
        def watcher():
            while True:
                self.scheduler.run_pending()
                time.sleep(10)

        t = Thread(target=watcher)
        t.daemon = True
        t.start()

    def consume(self):
        while not self.cache.empty():
            self.send(self.cache.get())


class TelegramNotifier(NotifierHandler):
    def __init__(self, app: Application, time=None):
        super().__init__(time)
        self.app = app

    def send(self, record):
        async def job(context: ContextTypes.DEFAULT_TYPE):
            notifier = record.get("extra", {}).get("notifier", {}).get("telegram", [])
            if not notifier:
                notifier = self.users
            for u in notifier:
                context.bot.send_message(record.message, chat_id=u)

        self.app.job_queue.run_once(job, 1)


class WecomChanNotifier(NotifierHandler):
    def __init__(
        self,
        corpid,
        agentid,
        secret,
        touser="@all",
        time=None,
        proxy_host=None,
        proxy_port=None,
        proxy_type=None,
    ):
        super().__init__(time)
        self.corpid = corpid
        self.agentid = agentid
        self.secret = secret
        self.touser = touser
        self.session = requests.session()
        if all((proxy_type, proxy_host, proxy_port)):
            proxy = f"{proxy_type}://{proxy_host}:{proxy_port}"
            self.session.proxies = {"http": proxy, "https": proxy}
        self._token = None
        self._fail_countdown = 5

    def _on_fail(self):
        self._fail_countdown -= 1
        if self._fail_countdown <= 0:
            self.disable = True

    def get_token(self, force=False):
        if self._token and not force:
            return self._token
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.corpid}&corpsecret={self.secret}"
        try:
            resp = json.loads(self.session.get(url).content)
            if not resp["errcode"] == 0:
                raise RuntimeError(resp.get("errmsg", "unknown"))
            return resp["access_token"]
        except Exception as e:
            logger.warning(f'无法连接到 "WecomChan" 提醒, 请检查设置. ({e})')
            self._on_fail()

    def send(self, record):
        for force in (False, True):
            token = self.get_token(force=force)
            if not token:
                continue
            url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
            data = {
                "touser": self.touser,
                "agentid": self.agentid,
                "msgtype": "text",
                "text": {"content": prefix(record.message)},
                "duplicate_check_interval": 600,
            }
            resp = self.session.post(url, data=json.dumps(data)).content
            if resp["errcode"] == 0:
                break
        else:
            logger.warning(f'通过 "WecomChan" 发送提醒失败.')
            self._on_fail()
