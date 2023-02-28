import random
import time
from pathlib import Path

from loguru import logger

from .base import AnswerBotCheckin


class PeachCheckin(AnswerBotCheckin):
    BOT_USER_ID = 5457506368
    BOT_NAME = "桃子"

    def _send_checkin(self, retry=False):
        super()._send_checkin(retry=retry, cmd="/start")

    def _update_handler(self, update):
        if "new_content" in update and update["chat_id"] == self.BOT_USER_ID:
            caption = update["new_content"]["caption"]["text"]
            self._on_text(caption)

    def _message_parser(self, message, ignore=()):
        if "photo" in message["content"]:
            photo = message["content"]["photo"]["sizes"][0]["photo"]
            caption = message["content"]["caption"]["text"]
            if "欢迎使用" in caption and "reply_markup" in message and "answer" not in ignore:
                self._message = message
                for row in message["reply_markup"]["rows"]:
                    for answer in row:
                        if "签到" in answer["text"]:
                            self._trigger_answer(answer, message=message)
                yield "answer"
            elif "请输入验证码" in caption and "captcha" not in ignore:
                path = Path(photo["local"]["path"])
                if path.is_file():
                    self._captcha_parser(path)
                else:
                    self._download_photo(photo)
                yield "captcha"
            else:
                if "text" not in ignore:
                    self._on_text(caption)
                    yield "text"

    def _on_captcha(self, captcha: str):
        logger.debug(self.msg(f"接收到Captcha: {captcha}"))
        time.sleep(random.randint(5, 10))
        if not captcha.strip():
            captcha = "unknown"
        ret = self.client.send_message(chat_id=self.BOT_USER_ID, text=captcha)
        ret.wait()
