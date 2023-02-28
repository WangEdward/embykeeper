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
        return wrapper
    return deco