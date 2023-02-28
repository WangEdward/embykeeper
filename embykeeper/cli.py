import time
from datetime import datetime
from pathlib import Path

import click
import schedule
import toml
from dateutil import parser
from loguru import logger
from rich.logging import Console, RichHandler

from .embywatcher import embywatcher
from .notifier import WecomChanNotifierHandler
from .telechecker import telechecker
from .utils import CommandWithOptionalFlagValues

logger.remove()
logger.add(RichHandler(console=Console(stderr=True)), format="{message}")


def _get_faked_config():
    import random
    import string
    import uuid

    from faker import Faker
    from faker.providers import internet, profile

    fake = Faker()
    fake.add_provider(internet)
    fake.add_provider(profile)
    config = {}
    config["timeout"] = 120
    config["retries"] = 10
    config["proxy"] = {
        "host": "127.0.0.1",
        "port": "1080",
        "type": "socks5",
    }
    config["notifier"] = {
        "time": "09:00AM",
        "wecomchan": {
            "corpid": "".join(random.choices(string.ascii_letters + string.digits, k=18)),
            "agentid": "1000002",
            "secret": fake.password(length=43),
            "touser": "@all",
            "useproxy": False,
        },
        "telegram": {
            "token": f'{fake.numerify(text="##########")}:{fake.password(length=35)}',
            "useproxy": True,
        },
    }
    config["telegram"] = []
    for _ in range(2):
        config["telegram"].append(
            {
                "api_id": fake.numerify(text="########"),
                "api_hash": uuid.uuid4().hex,
                "phone": f'+861{fake.numerify(text="##########")}',
            }
        )
    config["emby"] = []
    for _ in range(2):
        config["emby"].append(
            {
                "url": fake.url(["https"]),
                "username": fake.profile()["username"],
                "password": fake.password(),
                "useproxy": True,
            }
        )
    return config


def config_notifier(config):
    notifier = config.get("notifier", {})
    proxy = config.get("proxy", None)
    if proxy:
        proxy_host = proxy.get("host", "127.0.0.1")
        proxy_port = proxy.get("port", "1080")
        proxy_type = proxy.get("type", "socks5")
    else:
        proxy_host = proxy_port = proxy_type = None
    if "wecomchan" in notifier:
        conf = notifier["wecomchan"]
        up = conf.get("useproxy", False)
        handler = WecomChanNotifierHandler(
            corpid=conf["corpid"],
            agentid=conf["agentid"],
            secret=conf["secret"],
            touser=conf.get("touser", "@all"),
            proxy_host=proxy_host if up else None,
            proxy_port=proxy_port if up else None,
            proxy_type=proxy_type if up else None,
        )
        logger.add(handler, level="warning", format="{message}")
    if "telegram" in notifier:
        pass


@click.command(cls=CommandWithOptionalFlagValues)
@click.argument("config", required=False, type=click.Path(dir_okay=False, exists=True))
@click.option(
    "--telegram",
    "-t",
    type=str,
    flag_value="08:00",
    help="每日指定时间执行Telegram bot签到",
)
@click.option("--telegram-follow", is_flag=True, hidden=True, help="启动Telegram监听模式以确定ChatID")
@click.option("--emby", "-e", type=int, flag_value=7, help="每隔指定天数执行Emby保活")
@click.option("--instant/--no-instant", default=True, help="立刻执行一次计划任务")
@click.option("--quiet/--no-quiet", default=False, help="启用批处理模式并禁用输入, 可能导致无法输入验证码")
def cli(config, telegram, telegram_follow, emby, instant, quiet):
    if not config:
        logger.warning("需要输入一个toml格式的config文件.")
        default_config = "config.toml"
        if not Path(default_config).exists():
            with open(default_config, "w+") as f:
                toml.dump(_get_faked_config(), f)
                logger.warning(f'您可以根据生成的参考配置文件"{default_config}"进行配置')
        return
    with open(config) as f:
        config = toml.load(f)
    if quiet == True:
        config["quiet"] = True
    if telegram_follow:
        telechecker(config, follow=True)
    if not telegram and not emby:
        telegram = "08:00"
        emby = 7
    schedule_telegram = schedule.Scheduler()
    if telegram:
        telegram = parser.parse(telegram).time().strftime("%H:%M")
        schedule_telegram.every().day.at(telegram).do(telechecker, config=config)
    schedule_emby = schedule.Scheduler()
    if emby:
        schedule_emby.every(emby).days.at(datetime.now().strftime("%H:%M")).do(embywatcher, config=config)
    if instant:
        schedule_telegram.run_all()
        schedule_emby.run_all()
    if telegram:
        logger.info(f"下一次签到将在{int(schedule_telegram.idle_seconds/3600)}小时后进行.")
    if emby:
        logger.info(f"下一次保活将在{int(schedule_emby.idle_seconds/3600/24)}天后进行.")
    while True:
        schedule_telegram.run_pending()
        schedule_emby.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    cli()
