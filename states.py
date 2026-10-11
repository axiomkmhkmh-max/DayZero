from aiogram.fsm.state import State, StatesGroup


class Ask(StatesGroup):
    target = State()       # منتظر @یوزرنیم/آیدی هدف (data: action)
    alname = State()       # منتظر اسم اتحاد
