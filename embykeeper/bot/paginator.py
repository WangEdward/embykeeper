import re
from functools import wraps
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from ..utils import batch


class Paginator:
    format = {
        "first": "<<",
        "previous": "<",
        "current": "{current}/{total}",
        "next": ">",
        "last": ">>",
    }

    def __init__(self, name, lines=[], maxlen=15, minwidth=15, header=None, footer=None, page_callback=None):
        self.name = name
        self.minwidth = minwidth
        self.header = header
        self.footer = footer
        self.page_callback = page_callback
        self.set_pages(lines, maxlen=maxlen)

    def set_pages(self, lines, maxlen=15):
        if isinstance(lines, str):
            lines = lines.split("\n")
        if not lines:
            lines = ["<空>"]
        self.pages = list(batch(lines, size=maxlen))

    def build_keys(self, current=1):
        return InlineKeyboardMarkup([self.build_page_keys(current=current)])

    def build_page_keys(self, current=1):
        total = len(self.pages)
        keys = []
        if current > 1:
            keys.append("previous")
        if current > 2:
            keys.append("first")
        keys.append("current")
        if total > current:
            keys.append("next")
        if total - current > 1:
            keys.append("last")
        buttons = []
        for k, f in self.format.items():
            if k in keys:
                if k == "first":
                    page = 1
                elif k == "previous":
                    page = current - 1
                elif k == "current":
                    page = current
                elif k == "next":
                    page = current + 1
                elif k == "last":
                    page = total
                if k == "current":
                    callback = f"{self.name}#!"
                else:
                    callback = f"{self.name}#{page}"
                buttons.append(InlineKeyboardButton(f.format(current=page, total=total), callback_data=callback))
        return buttons

    def register_handler(self, app: Application):
        pattern = r"^{}#\d+$".format(self.name)
        for h in app.handlers:
            if isinstance(h, CallbackQueryHandler):
                if getattr(h, "pattern", None) == pattern:
                    break
        else:
            app.add_handler(CallbackQueryHandler(self.update_page, pattern=pattern))

    def get_page(self, current):
        page = []
        if self.header:
            page.append(self.header)
        page.extend(self.pages[current - 1])
        if self.footer:
            page.append(self.footer)
        page = [f"<pre>{escape(l.ljust(self.minwidth))}&#x200D;</pre>" for l in page]
        return "\n".join(page)

    async def send_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self.get_page(1), parse_mode=ParseMode.HTML, reply_markup=self.build_keys(1))

    async def update_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page=None):
        query = update.callback_query
        await query.answer()
        if not page:
            data = query.data.split("#")[-1]
            if data == "!":
                if self.page_callback:
                    return await self.page_callback(update, context)
            page = int(data)
        await query.edit_message_text(
            self.get_page(page), parse_mode=ParseMode.HTML, reply_markup=self.build_keys(page)
        )


class SelectPaginator(Paginator):
    format = {"previous": "<", "next": ">"}

    def __init__(self, name, items=[], select_callback=None, **kw):
        super().__init__(name, lines=list(items), maxlen=10, **kw)
        self.items = items
        self.select_callback = select_callback

    def build_keys(self, current=1):
        page_keys = self.build_page_keys(current)
        select_keys = [
            InlineKeyboardButton(i + 1, callback_data=f"{self.name}#{current}@{i + 1}")
            for i in range(len(self.pages[current - 1]))
        ]
        keys = []
        if page_keys:
            keys.append(page_keys)
        if select_keys:
            keys.extend(list(batch(select_keys, size=5)))
        return InlineKeyboardMarkup(keys)

    def set_pages(self, lines, maxlen=15):
        pages = []
        for b in batch(lines, size=maxlen):
            page = []
            for i, l in enumerate(b):
                page.append(f"{i + 1}. {l}")
            pages.append(page)
        self.pages = pages

    def callback_deco(self, func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            await query.answer()
            match = re.search(r"#(\d+)@(\d+)$", query.data)
            page, select = match.groups()
            index = (int(page) - 1) * 10 + int(select) - 1
            if isinstance(self.items, dict):
                items = list(self.items.values())
            else:
                items = self.items
            item = items[index]
            return await func(update, context, item=item)

        return wrapper

    def register_handler(self, app: Application):
        super().register_handler(app)
        if self.select_callback:
            pattern = r"^{}#\d+@\d+$".format(self.name)
            for h in app.handlers:
                if isinstance(h, CallbackQueryHandler):
                    if getattr(h, "pattern", None) == pattern:
                        break
            else:
                app.add_handler(CallbackQueryHandler(self.callback_deco(self.select_callback), pattern=pattern))
