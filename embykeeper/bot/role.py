from enum import EnumMeta, IntEnum

class _UserRoleMeta(EnumMeta):
    def __getitem__(self, item):
        if isinstance(item, str):
            item = item.split()[0].upper()
        return super().__getitem__(item)

class UserRole(IntEnum, metaclass=_UserRoleMeta):
    BLOCKED = 0
    USER = 20
    ADMIN = 50
    CREATOR = 100
    
    def is_admin(self):
        return self >= self.ADMIN
    
    def is_blocked(self):
        return self <= self.BLOCKED

class User:
    def __init__(self, telegram):
        self.role = Rol

    def auth()

def command(require=TelegramUserRole.USER):
    def deco(func):
        @wraps(func)
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