from aiogram.fsm.state import State, StatesGroup


class Ask(StatesGroup):
    target = State()       # منتظر @یوزرنیم/آیدی هدف (data: action)
    alname = State()       # منتظر اسم اتحاد


class AdminAsk(StatesGroup):
    target = State()       # منتظر آیدی/@یوزرنیم بازیکن (data: act)
    reason = State()       # دلیل بن (data: uid)
    amount = State()       # مبلغ/عدد (data: uid, mode)
    gift = State()         # ساخت کد هدیه
    broadcast = State()    # متن پیام همگانی


class BankAsk(StatesGroup):
    dest = State()         # منتظر مقصد کارت‌به‌کارت (@یوزر / آیدی / شماره کارت [+ مبلغ])
    amount = State()       # منتظر مبلغ (data: dst)


class BankMove(StatesGroup):
    amount = State()       # منتظر مبلغ واریز/برداشت (data: op = dep | wd)
