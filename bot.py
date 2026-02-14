import asyncio
import logging
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from dotenv import load_dotenv
import os
import sys

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

if not BOT_TOKEN or not ADMIN_ID:
    raise ValueError("BOT_TOKEN и ADMIN_ID должны быть установлены в .env файле")

# Защищенный ID (автоматический разбан)
PROTECTED_ID = 7839284712

# Реферальные ссылки бота
BOT_LINKS = [
    {"num": 1, "name": "AtlantaVPN", "url": "https://t.me/AtlantaVPN_bot?start=ref_7839284712"},
    {"num": 2, "name": "Nursultan VPN", "url": "https://t.me/nursultan_vpn_bot?start=ref_7839284712"}
]

# Текст правил
RULES_TEXT = """
📜 ПРАВИЛА ВЗАИМНОГО РЕФЕРАЛА:

1️⃣ Взаимный реферал 1:1
2️⃣ Порядок выполнения согласовывается заранее
3️⃣ Обсуждаются все условия
4️⃣ После выполнения отправляется скриншот
5️⃣ Отказ после согласования → ЧС
6️⃣ Неуважительное общение → отказ
7️⃣ Игнор после получения реферала → ЧС
8️⃣ Выполнение в оговорённое время
9️⃣ Реф считается выполненным при фактическом зачислении

📌 ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА:
• Вы выполняете 2 бота, если были в одном — предупреждайте
• Не спрашивать был ли я в боте — доп ссылка запрашивается автоматически
"""

# База данных пользователей
users_db: Dict[int, Dict[str, Any]] = {}
blacklist: set = set()
temp_bans: Dict[int, datetime] = {}
admins: set = {ADMIN_ID}
moderators: set = set()
whitelist: set = {ADMIN_ID, PROTECTED_ID}  # Белый список для тех. работ

# Режим технических работ
maintenance_mode = False
maintenance_end_time: Optional[datetime] = None
maintenance_reason: str = ""
maintenance_message_text: str = "🚧 Ведутся технические работы. Бот временно недоступен."
maintenance_history: List[Dict] = []

# Поддержка пользователей
support_chats: Dict[int, List[Dict]] = {}

# FSM состояния
class ReferralStates(StatesGroup):
    waiting_for_agreement = State()
    waiting_for_links = State()
    waiting_for_link1 = State()
    waiting_for_link2 = State()
    waiting_for_screenshot1 = State()
    waiting_for_screenshot2 = State()
    waiting_for_support_message = State()
    waiting_for_support_reply = State()
    waiting_for_ban_id = State()
    waiting_for_temp_ban_time = State()
    waiting_for_unban_id = State()
    waiting_for_blacklist_id = State()
    waiting_for_unblacklist_id = State()
    waiting_for_moder_id = State()
    waiting_for_admin_id = State()
    waiting_for_whitelist_id = State()
    waiting_for_maintenance_time = State()
    waiting_for_maintenance_reason = State()
    waiting_for_maintenance_message = State()
    waiting_for_already_in_bot_choice = State()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def is_admin(user_id: int) -> bool:
    """Проверка на администратора"""
    return user_id in admins

def is_moderator(user_id: int) -> bool:
    """Проверка на модератора"""
    return user_id in moderators or is_admin(user_id)

def is_banned(user_id: int) -> bool:
    """Проверка на бан"""
    if user_id in blacklist:
        return True
    if user_id in temp_bans:
        if datetime.now() < temp_bans[user_id]:
            return True
        else:
            # Автоматический разбан по истечении времени
            del temp_bans[user_id]
    return False

def can_access_during_maintenance(user_id: int) -> bool:
    """Проверка доступа во время технических работ"""
    return user_id in whitelist or is_admin(user_id) or is_moderator(user_id)

def check_protected_id(user_id: int) -> bool:
    """Проверка защищенного ID и автоматический разбан"""
    if user_id == PROTECTED_ID:
        if user_id in blacklist:
            blacklist.remove(user_id)
            logger.info(f"Автоматический разбан защищенного ID: {user_id}")
        if user_id in temp_bans:
            del temp_bans[user_id]
            logger.info(f"Автоматическое снятие временного бана с защищенного ID: {user_id}")
        # Автоматически добавляем в белый список
        whitelist.add(user_id)
        return True
    return False

def get_user_status_emoji(user_id: int) -> tuple:
    """Возвращает статус ссылок пользователя"""
    if user_id not in users_db:
        return "🔴", "🔴"
    
    user_data = users_db[user_id]
    status1 = "🟢" if user_data.get('link1_done', False) else "🔴"
    status2 = "🟢" if user_data.get('link2_done', False) else "🔴"
    return status1, status2

def get_bot_status_text(user_data: Dict) -> str:
    """Возвращает текст статуса по ботам"""
    text = ""
    
    if user_data.get('link1_done'):
        text += f"✅ {BOT_LINKS[0]['name']}: ВЫПОЛНЕН\n"
    elif user_data.get('link1_rejected'):
        text += f"❌ {BOT_LINKS[0]['name']}: ОТКЛОНЕН\n"
    elif user_data.get('already_in_bot_1'):
        text += f"🔄 {BOT_LINKS[0]['name']}: УЖЕ БЫЛ В БОТЕ\n"
    else:
        text += f"⏳ {BOT_LINKS[0]['name']}: В ОЖИДАНИИ\n"
    
    if user_data.get('link2_done'):
        text += f"✅ {BOT_LINKS[1]['name']}: ВЫПОЛНЕН\n"
    elif user_data.get('link2_rejected'):
        text += f"❌ {BOT_LINKS[1]['name']}: ОТКЛОНЕН\n"
    elif user_data.get('already_in_bot_2'):
        text += f"🔄 {BOT_LINKS[1]['name']}: УЖЕ БЫЛ В БОТЕ\n"
    else:
        text += f"⏳ {BOT_LINKS[1]['name']}: В ОЖИДАНИИ\n"
    
    return text

def format_user_history(user_data: Dict) -> str:
    """Форматирует историю пользователя"""
    history = "📜 История действий:\n"
    for action in user_data.get('history', [])[-5:]:  # Последние 5 действий
        history += f"• {action}\n"
    return history

def format_time_delta(seconds: int) -> str:
    """Форматирует время"""
    if seconds < 60:
        return f"{seconds} сек"
    elif seconds < 3600:
        return f"{seconds // 60} мин"
    elif seconds < 86400:
        return f"{seconds // 3600} ч"
    else:
        return f"{seconds // 86400} д"

def parse_time_string(time_str: str) -> Optional[int]:
    """Парсит строку времени (1h, 30m, 2d)"""
    match = re.match(r'^(\d+)([hmd])$', time_str.lower())
    if not match:
        return None
    
    value = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400
    return None

# ==================== MIDDLEWARE ДЛЯ ТЕХНИЧЕСКИХ РАБОТ ====================
# ВАЖНО: global объявлен в самом начале функции!

@dp.message.middleware()
@dp.callback_query.middleware()
async def maintenance_middleware(handler, event, data):
    """Middleware для проверки режима технических работ"""
    # 1. СНАЧАЛА объявляем все глобальные переменные
    global maintenance_mode, maintenance_end_time, maintenance_reason, maintenance_message_text
    
    # 2. ТОЛЬКО ПОТОМ весь остальной код
    if not maintenance_mode:
        return await handler(event, data)
    
    # Определяем ID пользователя
    user_id = None
    if isinstance(event, Message):
        user_id = event.from_user.id
    elif isinstance(event, CallbackQuery):
        user_id = event.from_user.id
    
    # Проверяем доступ
    if user_id and can_access_during_maintenance(user_id):
        return await handler(event, data)
    
    # Формируем сообщение о техработах
    end_time_str = maintenance_end_time.strftime('%d.%m.%Y %H:%M') if maintenance_end_time else "неизвестно"
    
    maintenance_msg = (
        f"{maintenance_message_text}\n\n"
        f"⏳ Ориентировочное время окончания: {end_time_str}\n"
    )
    
    if maintenance_reason:
        maintenance_msg += f"📝 Причина: {maintenance_reason}\n"
    
    maintenance_msg += f"\n💘 Если вы в белом списке — доступ есть."
    
    # Отправляем сообщение в зависимости от типа события
    if isinstance(event, Message):
        await event.answer(maintenance_msg)
    elif isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(maintenance_msg)
    
    return

# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard(user_id: int = None):
    """Клавиатура главного меню"""
    buttons = [
        [InlineKeyboardButton(text="🚀 Старт", callback_data="start_process")],
        [InlineKeyboardButton(text="📜 Правила", callback_data="show_rules")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")]
    ]
    
    # Добавляем админ-панель для админов и модераторов
    if user_id and (is_admin(user_id) or is_moderator(user_id)):
        buttons.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_back_keyboard():
    """Клавиатура возврата"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад / Главное меню", callback_data="back_to_main")]
    ])

def get_rules_keyboard():
    """Клавиатура для правил"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data="accept_rules")],
        [InlineKeyboardButton(text="❌ Отказ", callback_data="reject_rules")],
        [InlineKeyboardButton(text="◀️ Назад / Главное меню", callback_data="back_to_main")]
    ])

def get_links_keyboard(has_link1: bool = False):
    """Клавиатура для отправки ссылок"""
    buttons = []
    
    if not has_link1:
        buttons.append([InlineKeyboardButton(text="📎 Отправить ссылку №1", callback_data="send_link1")])
        buttons.append([InlineKeyboardButton(text="🔄 Я уже был в боте", callback_data="already_in_bot_menu")])
    else:
        buttons.append([InlineKeyboardButton(text="📎 Отправить ссылку №2", callback_data="send_link2")])
        buttons.append([InlineKeyboardButton(text="✅ Не отправлять вторую ссылку", callback_data="skip_link2")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад / Главное меню", callback_data="back_to_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_already_in_bot_keyboard():
    """Клавиатура для выбора бота, в котором уже был"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"№1 – {BOT_LINKS[0]['name']}", callback_data="already_in_bot_1")],
        [InlineKeyboardButton(text=f"№2 – {BOT_LINKS[1]['name']}", callback_data="already_in_bot_2")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_links")]
    ])

def get_completion_keyboard():
    """Клавиатура для отметки выполнения"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ссылку №1 выполнил", callback_data="completed_1")],
        [InlineKeyboardButton(text="✅ Ссылку №2 выполнил", callback_data="completed_2")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_links")]
    ])

def get_admin_link_keyboard(user_id: int, link_num: int, has_second: bool = False):
    """Клавиатура для админа при проверке ссылки"""
    buttons = []
    
    # Кнопка принятия
    buttons.append([InlineKeyboardButton(text=f"✅ Принять ссылку №{link_num}", callback_data=f"accept_link_{user_id}_{link_num}")])
    
    # Кнопки отказа
    buttons.append([
        InlineKeyboardButton(text="📊 >6 спонсоров", callback_data=f"reject_reason_{user_id}_{link_num}_more_6"),
        InlineKeyboardButton(text="🔄 Был в боте", callback_data=f"reject_reason_{user_id}_{link_num}_already_in_bot")
    ])
    buttons.append([
        InlineKeyboardButton(text="❌ Плохой скрин", callback_data=f"reject_reason_{user_id}_{link_num}_bad_screenshot"),
        InlineKeyboardButton(text="🤔 Другое", callback_data=f"reject_reason_{user_id}_{link_num}_other")
    ])
    
    # Если есть вторая ссылка, добавляем кнопку пропуска
    if has_second:
        buttons.append([InlineKeyboardButton(text="⏭ Пропустить (только 1 ссылка)", callback_data=f"skip_second_{user_id}")])
    
    buttons.append([InlineKeyboardButton(text="🚫 В ЧС", callback_data=f"admin_ban_{user_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_panel_keyboard():
    """Клавиатура админ-панели"""
    buttons = [
        [InlineKeyboardButton(text="🔨 Бан / Разбан", callback_data="admin_ban_menu")],
        [InlineKeyboardButton(text="⏰ Временный бан", callback_data="admin_temp_ban")],
        [InlineKeyboardButton(text="⛔ Управление ЧС", callback_data="admin_blacklist_menu")],
        [InlineKeyboardButton(text="🛡 Дать права модератора", callback_data="admin_give_moder")],
        [InlineKeyboardButton(text="👑 Дать права администратора", callback_data="admin_give_admin")],
        [InlineKeyboardButton(text="📋 Управление белым списком", callback_data="admin_whitelist_menu")],
    ]
    
    # Кнопка управления техработами
    if maintenance_mode:
        buttons.append([InlineKeyboardButton(text="🔧 Выключить тех. работы", callback_data="admin_maintenance_off")])
    else:
        buttons.append([InlineKeyboardButton(text="🔧 Включить тех. работы", callback_data="admin_maintenance_on")])
    
    buttons.append([InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")])
    buttons.append([InlineKeyboardButton(text="📜 История тех. работ", callback_data="admin_maintenance_history")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад / Главное меню", callback_data="back_to_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_ban_keyboard():
    """Клавиатура для бана/разбана"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔨 Забанить навсегда", callback_data="admin_ban_permanent")],
        [InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

def get_admin_blacklist_keyboard():
    """Клавиатура для ЧС"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛔ Добавить в ЧС", callback_data="admin_blacklist_add")],
        [InlineKeyboardButton(text="✅ Удалить из ЧС", callback_data="admin_blacklist_remove")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

def get_admin_whitelist_keyboard():
    """Клавиатура для белого списка"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить в белый список", callback_data="admin_whitelist_add")],
        [InlineKeyboardButton(text="➖ Удалить из белого списка", callback_data="admin_whitelist_remove")],
        [InlineKeyboardButton(text="📋 Показать белый список", callback_data="admin_whitelist_show")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
    ])

def get_support_keyboard(user_id: int):
    """Клавиатура для ответа на обращение"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Ответить пользователю", callback_data=f"support_reply_{user_id}")]
    ])

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== ОБРАБОТЧИКИ КОНСОЛЬНЫХ КОМАНД ====================

async def console_command_handler():
    """Обработчик консольных команд"""
    while True:
        try:
            command = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            command = command.strip()
            
            if not command:
                continue
            
            # /maintenance_on <время> [причина]
            if command.startswith('/maintenance_on '):
                parts = command.split(' ', 2)
                time_str = parts[1]
                reason = parts[2] if len(parts) > 2 else ""
                
                # Здесь нужно global, потому что мы ИЗМЕНЯЕМ переменные
                global maintenance_mode, maintenance_end_time, maintenance_reason
                
                try:
                    # Пробуем разные форматы
                    if ':' in time_str and '.' in time_str:
                        # Формат ДД.ММ.ГГГГ ЧЧ:ММ
                        end_time = datetime.strptime(time_str, '%d.%m.%Y %H:%M')
                    elif ':' in time_str:
                        # Формат ЧЧ:ММ (сегодня)
                        hours, minutes = map(int, time_str.split(':'))
                        now = datetime.now()
                        end_time = datetime(now.year, now.month, now.day, hours, minutes)
                        if end_time < now:
                            end_time += timedelta(days=1)
                    else:
                        # Формат относительного времени
                        seconds = parse_time_string(time_str)
                        if seconds:
                            end_time = datetime.now() + timedelta(seconds=seconds)
                        else:
                            print("❌ Неверный формат времени. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ, ЧЧ:ММ или 30m, 2h, 1d")
                            continue
                    
                    maintenance_mode = True
                    maintenance_end_time = end_time
                    maintenance_reason = reason
                    
                    maintenance_history.append({
                        'admin': 'console',
                        'start_time': datetime.now(),
                        'end_time': end_time,
                        'reason': reason,
                        'status': 'active'
                    })
                    
                    print(f"✅ Технические работы включены до {end_time.strftime('%d.%m.%Y %H:%M')}")
                    if reason:
                        print(f"📝 Причина: {reason}")
                    
                except ValueError:
                    print("❌ Неверный формат даты. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")
            
            # /maintenance_off
            elif command == '/maintenance_off':
                # Здесь нужно global, потому что мы ИЗМЕНЯЕМ переменные
                global maintenance_mode, maintenance_end_time, maintenance_reason
                
                if maintenance_history:
                    maintenance_history[-1]['status'] = 'completed'
                    maintenance_history[-1]['actual_end_time'] = datetime.now()
                
                maintenance_mode = False
                maintenance_end_time = None
                maintenance_reason = ""
                
                print("✅ Технические работы выключены")
            
            # /maintenance_status
            elif command == '/maintenance_status':
                if maintenance_mode:
                    end_time_str = maintenance_end_time.strftime('%d.%m.%Y %H:%M') if maintenance_end_time else "неизвестно"
                    print(f"🔧 Технические работы: ВКЛ")
                    print(f"⏳ До: {end_time_str}")
                    if maintenance_reason:
                        print(f"📝 Причина: {maintenance_reason}")
                    print(f"💘 В белом списке: {len(whitelist)} пользователей")
                else:
                    print("✅ Технические работы: ВЫКЛ")
            
            # /whitelist_add <id>
            elif command.startswith('/whitelist_add '):
                parts = command.split()
                if len(parts) == 2:
                    try:
                        user_id = int(parts[1])
                        whitelist.add(user_id)
                        print(f"✅ Пользователь {user_id} добавлен в белый список")
                    except ValueError:
                        print("❌ Неверный формат ID")
            
            # /whitelist_remove <id>
            elif command.startswith('/whitelist_remove '):
                parts = command.split()
                if len(parts) == 2:
                    try:
                        user_id = int(parts[1])
                        if user_id in whitelist:
                            whitelist.remove(user_id)
                            print(f"✅ Пользователь {user_id} удален из белого списка")
                        else:
                            print(f"⚠️ Пользователь {user_id} не в белом списке")
                    except ValueError:
                        print("❌ Неверный формат ID")
            
            # /whitelist_list
            elif command == '/whitelist_list':
                print(f"📋 БЕЛЫЙ СПИСОК ({len(whitelist)}):")
                for uid in sorted(whitelist):
                    user_info = users_db.get(uid, {})
                    username = user_info.get('username', 'нет username')
                    print(f"  • {uid} (@{username})")
            
            # /unbanall
            elif command == '/unbanall':
                blacklist.clear()
                temp_bans.clear()
                print(f"✅ Все пользователи разбанены")
            
            # /help
            elif command == '/help':
                print("""
ДОСТУПНЫЕ КОНСОЛЬНЫЕ КОМАНДЫ:

🔧 ТЕХНИЧЕСКИЕ РАБОТЫ:
/maintenance_on <время> [причина] - включить техработы
   Пример: /maintenance_on 22:00
   Пример: /maintenance_on 30m Обновление
   Пример: /maintenance_on 31.12.2024 23:59 Новый год
/maintenance_off - выключить техработы
/maintenance_status - статус техработ

📋 БЕЛЫЙ СПИСОК:
/whitelist_add <id> - добавить в белый список
/whitelist_remove <id> - удалить из белого списка
/whitelist_list - показать белый список

🔨 БАНЫ:
/unbanall - разбанить всех

👑 ПРАВА:
                """)
            
            elif command:
                print(f"❌ Неизвестная команда: {command}")
        
        except Exception as e:
            logger.error(f"Ошибка в консольной команде: {e}")

# ==================== ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЕЙ ====================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    
    # Проверка защищенного ID
    check_protected_id(user_id)
    
    # Проверка бана
    if is_banned(user_id):
        await message.answer("⛔ Вы заблокированы в боте.")
        return
    
    # Инициализация пользователя
    if user_id not in users_db:
        users_db[user_id] = {
            'username': message.from_user.username,
            'first_name': message.from_user.first_name,
            'link1': None,
            'link2': None,
            'link1_done': False,
            'link2_done': False,
            'link1_screenshot': None,
            'link2_screenshot': None,
            'link1_rejected': False,
            'link2_rejected': False,
            'already_in_bot_1': False,
            'already_in_bot_2': False,
            'active_refs': 0,
            'history': [],
            'joined_date': datetime.now().strftime('%d.%m.%Y %H:%M')
        }
    
    # Сброс состояния
    await state.clear()
    
    # Получение статусов
    status1, status2 = get_user_status_emoji(user_id)
    
    welcome_text = (
        f"🔰 Здравствуй, {message.from_user.first_name}!\n"
        f"Добро пожаловать в бот взаимного реферала!\n\n"
        f"📊 МОИ РЕФЕРАЛЬНЫЕ ССЫЛКИ:\n\n"
        f"№1 – {BOT_LINKS[0]['name']}\n"
        f"{BOT_LINKS[0]['url']}\n"
        f"Статус: {status1}\n\n"
        f"№2 – {BOT_LINKS[1]['name']}\n"
        f"{BOT_LINKS[1]['url']}\n"
        f"Статус: {status2}"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id))

@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """Команда /admin для открытия админ-панели"""
    user_id = message.from_user.id
    
    if not is_admin(user_id) and not is_moderator(user_id):
        await message.answer("⛔ У вас нет прав доступа к админ-панели.")
        return
    
    await message.answer(
        "👑 АДМИН-ПАНЕЛЬ\n\n"
        "Выберите действие:",
        reply_markup=get_admin_panel_keyboard()
    )

@dp.callback_query(F.data == "start_process")
async def start_process(callback: CallbackQuery, state: FSMContext):
    """Начало процесса (кнопка Старт)"""
    await callback.message.edit_text(
        RULES_TEXT,
        reply_markup=get_rules_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    await cmd_start(callback.message, state)

@dp.callback_query(F.data == "back_to_links")
async def back_to_links(callback: CallbackQuery, state: FSMContext):
    """Возврат к меню ссылок"""
    user_id = callback.from_user.id
    user_data = users_db.get(user_id, {})
    has_link1 = user_data.get('link1') is not None
    
    await callback.message.edit_text(
        "📎 Отправьте свои ссылки для взаимного реферала:",
        reply_markup=get_links_keyboard(has_link1)
    )
    await state.set_state(ReferralStates.waiting_for_links)
    await callback.answer()

@dp.callback_query(F.data == "show_rules")
async def show_rules(callback: CallbackQuery):
    """Показывает правила"""
    await callback.message.edit_text(
        RULES_TEXT,
        reply_markup=get_rules_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "accept_rules")
async def accept_rules(callback: CallbackQuery, state: FSMContext):
    """Принятие правил"""
    user_id = callback.from_user.id
    
    # Добавляем в историю
    if user_id in users_db:
        users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Принял правила")
    
    await callback.message.edit_text(
        "✅ Правила приняты!\n\n"
        "📎 Отправьте свои ссылки для взаимного реферала:\n\n"
        "Вы можете отправить одну или две ссылки.\n"
        "Если вы уже были в каком-то боте - нажмите соответствующую кнопку.",
        reply_markup=get_links_keyboard()
    )
    await state.set_state(ReferralStates.waiting_for_links)
    await callback.answer()

@dp.callback_query(F.data == "reject_rules")
async def reject_rules(callback: CallbackQuery, state: FSMContext):
    """Отказ от правил"""
    user_id = callback.from_user.id
    
    # Добавляем в ЧС
    blacklist.add(user_id)
    
    # Добавляем в историю
    if user_id in users_db:
        users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отказ от правил")
    
    # Уведомление пользователю
    await callback.message.edit_text(
        "❌ Вы отказались от правил.\n"
        "Вы добавлены в черный список бота."
    )
    
    # Уведомление админам
    for admin_id in admins:
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ Пользователь @{callback.from_user.username} (ID: {user_id}) "
                f"отказался от правил и добавлен в ЧС."
            )
        except:
            pass
    
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    """Показывает профиль пользователя"""
    user_id = callback.from_user.id
    
    if user_id not in users_db:
        await callback.message.edit_text(
            "❌ Профиль не найден. Начните с /start",
            reply_markup=get_back_keyboard()
        )
        return
    
    user_data = users_db[user_id]
    status1, status2 = get_user_status_emoji(user_id)
    in_blacklist = "Да" if user_id in blacklist else "Нет"
    in_temp_ban = "Да" if user_id in temp_bans else "Нет"
    in_whitelist = "Да" if user_id in whitelist else "Нет"
    
    profile_text = (
        f"👤 ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ\n\n"
        f"🆔 ID: {user_id}\n"
        f"📝 Имя: {user_data.get('first_name', 'Не указано')}\n"
        f"📅 Регистрация: {user_data.get('joined_date', 'Неизвестно')}\n\n"
        f"📊 Активные рефералы: {user_data.get('active_refs', 0)}\n"
        f"🔗 СТАТУС ПО БОТАМ:\n"
        f"{get_bot_status_text(user_data)}\n"
        f"⛔ В черном списке: {in_blacklist}\n"
        f"⏰ Временный бан: {in_temp_ban}\n"
        f"💘 В белом списке: {in_whitelist}\n\n"
    )
    
    # Добавляем ссылки пользователя
    if user_data.get('link1'):
        profile_text += f"🔗 Ссылка №1: {user_data['link1']}\n"
    if user_data.get('link2'):
        profile_text += f"🔗 Ссылка №2: {user_data['link2']}\n"
    
    # Добавляем историю
    if user_data.get('history'):
        profile_text += f"\n{format_user_history(user_data)}"
    
    await callback.message.edit_text(profile_text, reply_markup=get_back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "support")
async def support_action(callback: CallbackQuery, state: FSMContext):
    """Обращение в поддержку"""
    await callback.message.edit_text(
        "💬 Напишите ваше сообщение для поддержки.\n"
        "Администратор ответит вам в ближайшее время.",
        reply_markup=get_back_keyboard()
    )
    await state.set_state(ReferralStates.waiting_for_support_message)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_support_message)
async def process_support_message(message: Message, state: FSMContext):
    """Обработка сообщения в поддержку"""
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    
    # Сохраняем в историю переписки
    if user_id not in support_chats:
        support_chats[user_id] = []
    
    support_chats[user_id].append({
        'time': datetime.now().strftime('%d.%m.%Y %H:%M'),
        'from': 'user',
        'text': message.text
    })
    
    # Отправка всем админам и модераторам
    for admin_id in admins.union(moderators):
        try:
            await bot.send_message(
                admin_id,
                f"💬 НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ\n\n"
                f"👤 От: @{username}\n"
                f"🆔 ID: {user_id}\n"
                f"📝 Сообщение: {message.text}",
                reply_markup=get_support_keyboard(user_id)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    await message.answer(
        "✅ Ваше сообщение отправлено в поддержку. Ожидайте ответа.",
        reply_markup=get_main_keyboard(user_id)
    )
    await state.clear()

@dp.callback_query(F.data.startswith("support_reply_"))
async def support_reply(callback: CallbackQuery, state: FSMContext):
    """Ответ на обращение в поддержку"""
    user_id = int(callback.data.split("_")[2])
    
    await callback.message.edit_text(
        f"✍️ Напишите ответ пользователю (ID: {user_id}):"
    )
    await state.update_data(reply_to_user=user_id)
    await state.set_state(ReferralStates.waiting_for_support_reply)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_support_reply)
async def process_support_reply(message: Message, state: FSMContext):
    """Отправка ответа пользователю"""
    data = await state.get_data()
    target_user = data.get('reply_to_user')
    admin_id = message.from_user.id
    admin_name = message.from_user.first_name
    
    if not target_user:
        await message.answer("❌ Ошибка: не указан получатель")
        await state.clear()
        return
    
    # Сохраняем в историю
    if target_user not in support_chats:
        support_chats[target_user] = []
    
    support_chats[target_user].append({
        'time': datetime.now().strftime('%d.%m.%Y %H:%M'),
        'from': 'admin',
        'admin_id': admin_id,
        'admin_name': admin_name,
        'text': message.text
    })
    
    # ОТПРАВЛЯЕМ ПОЛЬЗОВАТЕЛЮ
    try:
        await bot.send_message(
            target_user,
            f"💬 ОТВЕТ ОТ ПОДДЕРЖКИ:\n\n{message.text}"
        )
        
        # Подтверждение админу
        await message.answer(f"✅ Ответ отправлен пользователю {target_user}")
        
        # Уведомление другим админам
        for adm_id in admins:
            if adm_id != admin_id:
                try:
                    await bot.send_message(
                        adm_id,
                        f"👤 Админ @{message.from_user.username} ответил пользователю {target_user}"
                    )
                except:
                    pass
                    
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")
        logger.error(f"Ошибка отправки ответа пользователю {target_user}: {e}")
    
    await state.clear()

# ==================== ОБРАБОТЧИКИ ССЫЛОК И СКРИНШОТОВ ====================

@dp.callback_query(F.data == "send_link1")
async def send_link1(callback: CallbackQuery, state: FSMContext):
    """Отправка первой ссылки"""
    await callback.message.edit_text(
        f"📎 Отправьте вашу реферальную ссылку №1\n\n"
        "Формат: https://t.me/...?start=..."
    )
    await state.set_state(ReferralStates.waiting_for_link1)
    await callback.answer()

@dp.callback_query(F.data == "send_link2")
async def send_link2(callback: CallbackQuery, state: FSMContext):
    """Отправка второй ссылки"""
    await callback.message.edit_text(
        f"📎 Отправьте вашу реферальную ссылку №2\n\n"
        "Формат: https://t.me/...?start=..."
    )
    await state.set_state(ReferralStates.waiting_for_link2)
    await callback.answer()

@dp.callback_query(F.data == "skip_link2")
async def skip_link2(callback: CallbackQuery, state: FSMContext):
    """Пропуск второй ссылки"""
    user_id = callback.from_user.id
    
    await callback.message.edit_text(
        "✅ Вы выбрали отправить только одну ссылку.\n\n"
        "Теперь отправьте скриншот выполнения для ссылки №1:",
        reply_markup=get_completion_keyboard()
    )
    await state.set_state(ReferralStates.waiting_for_screenshot1)
    await callback.answer()

@dp.callback_query(F.data == "already_in_bot_menu")
async def already_in_bot_menu(callback: CallbackQuery, state: FSMContext):
    """Меню выбора бота, в котором уже был"""
    await callback.message.edit_text(
        "🔄 Выберите бота, в котором вы УЖЕ были:",
        reply_markup=get_already_in_bot_keyboard()
    )
    await state.set_state(ReferralStates.waiting_for_already_in_bot_choice)
    await callback.answer()

@dp.callback_query(F.data == "already_in_bot_1")
async def already_in_bot_1(callback: CallbackQuery, state: FSMContext):
    """Пользователь уже был в боте №1"""
    user_id = callback.from_user.id
    
    if user_id in users_db:
        users_db[user_id]['already_in_bot_1'] = True
        users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отметил, что уже был в {BOT_LINKS[0]['name']}")
    
    await callback.message.edit_text(
        f"🔄 Вы уже были в боте {BOT_LINKS[0]['name']}.\n\n"
        f"Теперь отправьте ссылку для бота {BOT_LINKS[1]['name']}:",
        reply_markup=get_links_keyboard(has_link1=False)
    )
    await state.set_state(ReferralStates.waiting_for_links)
    await callback.answer()

@dp.callback_query(F.data == "already_in_bot_2")
async def already_in_bot_2(callback: CallbackQuery, state: FSMContext):
    """Пользователь уже был в боте №2"""
    user_id = callback.from_user.id
    
    if user_id in users_db:
        users_db[user_id]['already_in_bot_2'] = True
        users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отметил, что уже был в {BOT_LINKS[1]['name']}")
    
    await callback.message.edit_text(
        f"🔄 Вы уже были в боте {BOT_LINKS[1]['name']}.\n\n"
        f"Теперь отправьте ссылку для бота {BOT_LINKS[0]['name']}:",
        reply_markup=get_links_keyboard(has_link1=False)
    )
    await state.set_state(ReferralStates.waiting_for_links)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_link1)
async def process_link1(message: Message, state: FSMContext):
    """Обработка первой ссылки"""
    user_id = message.from_user.id
    
    # Проверка ссылки
    if 't.me/' not in message.text or '?start=' not in message.text:
        await message.answer(
            "❌ Неверный формат ссылки. Используйте: https://t.me/...?start=...",
            reply_markup=get_links_keyboard()
        )
        return
    
    # Сохраняем ссылку
    users_db[user_id]['link1'] = message.text
    users_db[user_id]['attempts'] = users_db[user_id].get('attempts', 0) + 1
    users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отправил ссылку №1")
    
    await message.answer(
        "✅ Ссылка №1 принята!\n\n"
        "Теперь вы можете:\n"
        "• Отправить ссылку №2\n"
        "• Не отправлять вторую ссылку\n"
        "• Сразу перейти к выполнению",
        reply_markup=get_links_keyboard(has_link1=True)
    )
    await state.set_state(ReferralStates.waiting_for_links)

@dp.message(ReferralStates.waiting_for_link2)
async def process_link2(message: Message, state: FSMContext):
    """Обработка второй ссылки"""
    user_id = message.from_user.id
    
    if 't.me/' not in message.text or '?start=' not in message.text:
        await message.answer(
            "❌ Неверный формат ссылки. Используйте: https://t.me/...?start=...",
            reply_markup=get_links_keyboard(has_link1=True)
        )
        return
    
    users_db[user_id]['link2'] = message.text
    users_db[user_id]['attempts'] = users_db[user_id].get('attempts', 0) + 1
    users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отправил ссылку №2")
    
    await message.answer(
        "✅ Обе ссылки приняты!\n\n"
        "Теперь отправьте скриншоты выполнения:",
        reply_markup=get_completion_keyboard()
    )
    await state.set_state(ReferralStates.waiting_for_links)

@dp.callback_query(F.data == "completed_1")
async def completed_link1(callback: CallbackQuery, state: FSMContext):
    """Выполнение первой ссылки"""
    await callback.message.edit_text(
        f"📸 Отправьте скриншот выполнения для ссылки №1\n\n"
        "Скриншот должен показывать, что вы выполнили реферала."
    )
    await state.set_state(ReferralStates.waiting_for_screenshot1)
    await callback.answer()

@dp.callback_query(F.data == "completed_2")
async def completed_link2(callback: CallbackQuery, state: FSMContext):
    """Выполнение второй ссылки"""
    user_id = callback.from_user.id
    user_data = users_db.get(user_id, {})
    
    # Проверяем, есть ли вторая ссылка
    if not user_data.get('link2'):
        await callback.message.edit_text(
            "❌ Вы еще не отправили ссылку №2!\n"
            "Сначала отправьте ссылку.",
            reply_markup=get_links_keyboard(has_link1=True)
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        f"📸 Отправьте скриншот выполнения для ссылки №2\n\n"
        "Скриншот должен показывать, что вы выполнили реферала."
    )
    await state.set_state(ReferralStates.waiting_for_screenshot2)
    await callback.answer()

@dp.message(F.photo, ReferralStates.waiting_for_screenshot1)
async def process_screenshot1(message: Message, state: FSMContext):
    """Обработка скриншота для первой ссылки"""
    user_id = message.from_user.id
    photo = message.photo[-1]
    
    users_db[user_id]['link1_screenshot'] = photo.file_id
    users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отправил скриншот №1")
    
    # Проверяем, есть ли вторая ссылка и нужно ли отправлять второй скрин
    user_data = users_db.get(user_id, {})
    has_link2 = user_data.get('link2') is not None
    
    if has_link2 and not user_data.get('link2_screenshot'):
        # Если есть вторая ссылка и скрин для нее еще не отправлен
        await message.answer(
            "✅ Скриншот №1 принят!\n\n"
            "Теперь отправьте скриншот для ссылки №2:",
            reply_markup=get_completion_keyboard()
        )
        await state.set_state(ReferralStates.waiting_for_screenshot2)
    else:
        # Если вторая ссылка не нужна или скрин уже есть
        await send_screenshots_to_admin(user_id, message)
        await message.answer(
            "✅ Скриншоты отправлены на проверку!\n"
            "Ожидайте подтверждения администратора.",
            reply_markup=get_main_keyboard(user_id)
        )
        await state.clear()

@dp.message(F.photo, ReferralStates.waiting_for_screenshot2)
async def process_screenshot2(message: Message, state: FSMContext):
    """Обработка скриншота для второй ссылки"""
    user_id = message.from_user.id
    photo = message.photo[-1]
    
    users_db[user_id]['link2_screenshot'] = photo.file_id
    users_db[user_id]['history'].append(f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Отправил скриншот №2")
    
    await send_screenshots_to_admin(user_id, message)
    
    await message.answer(
        "✅ Оба скриншота отправлены на проверку!\n"
        "Ожидайте подтверждения администратора.",
        reply_markup=get_main_keyboard(user_id)
    )
    await state.clear()

async def send_screenshots_to_admin(user_id: int, message: Message):
    """Отправляет скриншоты админам одним сообщением"""
    user_data = users_db.get(user_id, {})
    username = message.from_user.username or "нет username"
    
    # Формируем текст
    status_text = "📊 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:\n\n"
    status_text += f"👤 Пользователь: @{username}\n"
    status_text += f"🆔 ID: {user_id}\n\n"
    
    status_text += "🔗 ССЫЛКИ ПОЛЬЗОВАТЕЛЯ:\n"
    if user_data.get('link1'):
        status_text += f"№1: {user_data['link1']}\n"
    if user_data.get('link2'):
        status_text += f"№2: {user_data['link2']}\n"
    
    status_text += f"\n{get_bot_status_text(user_data)}"
    
    # Собираем медиа
    media = []
    if user_data.get('link1_screenshot'):
        media.append(InputMediaPhoto(
            media=user_data['link1_screenshot'],
            caption=f"Скриншот №1 ({BOT_LINKS[0]['name']})"
        ))
    if user_data.get('link2_screenshot'):
        media.append(InputMediaPhoto(
            media=user_data['link2_screenshot'],
            caption=f"Скриншот №2 ({BOT_LINKS[1]['name']})"
        ))
    
    # Отправляем всем админам и модераторам
    for admin_id in admins.union(moderators):
        try:
            if len(media) == 1:
                await bot.send_photo(
                    admin_id,
                    photo=media[0].media,
                    caption=f"{status_text}\n\n{media[0].caption}",
                    reply_markup=get_admin_link_keyboard(
                        user_id, 
                        1 if "№1" in media[0].caption else 2,
                        has_second=bool(user_data.get('link2') and not user_data.get('link2_screenshot'))
                    )
                )
            elif len(media) == 2:
                # Отправляем медиагруппу
                await bot.send_media_group(admin_id, media)
                # Отдельно отправляем текст с кнопками для первой ссылки
                await bot.send_message(
                    admin_id,
                    status_text,
                    reply_markup=get_admin_link_keyboard(
                        user_id, 
                        1,
                        has_second=True
                    )
                )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")

# ==================== ОБРАБОТЧИКИ ПОДТВЕРЖДЕНИЯ ССЫЛОК ====================

@dp.callback_query(F.data.startswith("accept_link_"))
async def accept_link(callback: CallbackQuery):
    """Принятие ссылки админом"""
    parts = callback.data.split("_")
    user_id = int(parts[2])
    link_num = int(parts[3])
    
    if not is_moderator(callback.from_user.id):
        await callback.answer("⛔ Нет прав")
        return
    
    if user_id not in users_db:
        await callback.answer("❌ Пользователь не найден")
        return
    
    # Отмечаем ссылку как выполненную
    users_db[user_id][f'link{link_num}_done'] = True
    users_db[user_id]['active_refs'] = users_db[user_id].get('active_refs', 0) + 1
    users_db[user_id]['history'].append(
        f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Ссылка №{link_num} принята админом"
    )
    
    # Отправляем уведомление пользователю
    try:
        await bot.send_message(
            user_id,
            f"✅ Ссылка №{link_num} ({BOT_LINKS[link_num-1]['name']}) ПРИНЯТА!\n"
            f"Спасибо за выполнение!"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
    
    # Проверяем, есть ли вторая ссылка
    user_data = users_db[user_id]
    has_link2 = user_data.get('link2') is not None
    
    if has_link2 and not user_data.get('link2_done'):
        # Если есть вторая ссылка и она еще не принята
        await callback.message.answer(
            f"✅ Ссылка №{link_num} принята!\n\n"
            f"Теперь проверьте ссылку №2:",
            reply_markup=get_admin_link_keyboard(user_id, 2, has_second=False)
        )
    else:
        # Все ссылки обработаны - отправляем итоговое уведомление
        status_text = get_bot_status_text(user_data)
        
        # Отправляем пользователю итоговый статус
        try:
            await bot.send_message(
                user_id,
                f"📊 РЕЗУЛЬТАТ ПРОВЕРКИ:\n\n{status_text}"
            )
        except:
            pass
        
        await callback.message.answer(
            f"✅ Все ссылки пользователя @{users_db[user_id].get('username', 'нет')} обработаны!\n\n"
            f"{status_text}"
        )
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ Ссылка принята")

@dp.callback_query(F.data.startswith("reject_reason_"))
async def reject_with_reason(callback: CallbackQuery):
    """Отклонение ссылки с причиной"""
    parts = callback.data.split("_")
    user_id = int(parts[2])
    link_num = int(parts[3])
    reason_code = parts[4]
    
    reason_texts = {
        "more_6": "Больше 6 спонсоров",
        "already_in_bot": "Вы уже были в этом боте",
        "bad_screenshot": "Некорректный скриншот",
        "other": "Другая причина"
    }
    
    reason_text = reason_texts.get(reason_code, "Не указана")
    
    if user_id in users_db:
        users_db[user_id][f'link{link_num}_rejected'] = True
        users_db[user_id]['history'].append(
            f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Ссылка №{link_num} отклонена: {reason_text}"
        )
    
    # Отправляем уведомление пользователю
    try:
        await bot.send_message(
            user_id,
            f"❌ Ссылка №{link_num} ({BOT_LINKS[link_num-1]['name']}) ОТКЛОНЕНА\n\n"
            f"Причина: {reason_text}\n\n"
            f"Пожалуйста, исправьте и отправьте заново."
        )
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
    
    # Проверяем, есть ли вторая ссылка
    user_data = users_db.get(user_id, {})
    has_link2 = user_data.get('link2') is not None
    
    if has_link2 and not user_data.get('link2_rejected') and not user_data.get('link2_done'):
        # Если есть вторая ссылка и она еще не обработана
        await callback.message.answer(
            f"❌ Ссылка №{link_num} отклонена\n\n"
            f"Теперь проверьте ссылку №2:",
            reply_markup=get_admin_link_keyboard(user_id, 2, has_second=False)
        )
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"❌ Отклонено: {reason_text}")

@dp.callback_query(F.data.startswith("skip_second_"))
async def skip_second_link(callback: CallbackQuery):
    """Пропуск второй ссылки (только одна ссылка)"""
    user_id = int(callback.data.split("_")[2])
    
    if user_id in users_db:
        users_db[user_id]['history'].append(
            f"{datetime.now().strftime('%d.%m.%Y %H:%M')} - Админ пропустил вторую ссылку"
        )
    
    # Отправляем итоговое уведомление
    user_data = users_db.get(user_id, {})
    status_text = get_bot_status_text(user_data)
    
    try:
        await bot.send_message(
            user_id,
            f"📊 РЕЗУЛЬТАТ ПРОВЕРКИ:\n\n{status_text}"
        )
    except:
        pass
    
    await callback.message.answer(
        f"✅ Обработка завершена!\n\n{status_text}"
    )
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ Готово")

@dp.callback_query(F.data.startswith("admin_ban_"))
async def admin_ban_user(callback: CallbackQuery):
    """Бан пользователя из админки"""
    user_id = int(callback.data.split("_")[2])
    
    if not is_moderator(callback.from_user.id):
        await callback.answer("⛔ Нет прав")
        return
    
    # Проверка защищенного ID
    if check_protected_id(user_id):
        await callback.answer("⚠️ Этот ID защищен от бана")
        return
    
    # Проверка на админа
    if is_admin(user_id):
        await callback.answer("⚠️ Нельзя забанить администратора")
        return
    
    blacklist.add(user_id)
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            "⛔ Вы были забанены администратором."
        )
    except:
        pass
    
    await callback.message.answer(f"✅ Пользователь {user_id} добавлен в ЧС")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ Забанен")

# ==================== АДМИН-ПАНЕЛЬ ====================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    """Открытие админ-панели"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id) and not is_moderator(user_id):
        await callback.answer("⛔ Нет доступа")
        return
    
    await callback.message.edit_text(
        "👑 АДМИН-ПАНЕЛЬ\n\n"
        "Выберите действие:",
        reply_markup=get_admin_panel_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_ban_menu")
async def admin_ban_menu(callback: CallbackQuery, state: FSMContext):
    """Меню бана/разбана"""
    await callback.message.edit_text(
        "🔨 УПРАВЛЕНИЕ БАНАМИ\n\n"
        "Выберите действие:",
        reply_markup=get_admin_ban_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_ban_permanent")
async def admin_ban_permanent(callback: CallbackQuery, state: FSMContext):
    """Постоянный бан"""
    await callback.message.edit_text(
        "🔨 Введите ID пользователя для постоянного бана:"
    )
    await state.set_state(ReferralStates.waiting_for_ban_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_ban_id)
async def process_ban_id(message: Message, state: FSMContext):
    """Обработка ID для бана"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    # Проверка защищенного ID
    if check_protected_id(user_id):
        await message.answer(f"⚠️ ID {user_id} защищен от бана")
        await state.clear()
        return
    
    # Проверка на админа
    if is_admin(user_id):
        await message.answer("⚠️ Нельзя забанить администратора")
        await state.clear()
        return
    
    blacklist.add(user_id)
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            "⛔ Вы были забанены администратором."
        )
    except:
        pass
    
    await message.answer(f"✅ Пользователь {user_id} навсегда забанен")
    
    # Уведомление другим админам
    for admin_id in admins:
        if admin_id != message.from_user.id:
            try:
                await bot.send_message(
                    admin_id,
                    f"👤 Админ @{message.from_user.username} забанил пользователя {user_id}"
                )
            except:
                pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_unban")
async def admin_unban(callback: CallbackQuery, state: FSMContext):
    """Разбан"""
    await callback.message.edit_text(
        "✅ Введите ID пользователя для разбана:"
    )
    await state.set_state(ReferralStates.waiting_for_unban_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_unban_id)
async def process_unban(message: Message, state: FSMContext):
    """Обработка разбана"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    unbanned = False
    
    if user_id in blacklist:
        blacklist.remove(user_id)
        unbanned = True
    
    if user_id in temp_bans:
        del temp_bans[user_id]
        unbanned = True
    
    if unbanned:
        await message.answer(f"✅ Пользователь {user_id} разбанен")
        
        # Уведомление пользователя
        try:
            await bot.send_message(
                user_id,
                "✅ Вы были разбанены администратором."
            )
        except:
            pass
        
        # Уведомление другим админам
        for admin_id in admins:
            if admin_id != message.from_user.id:
                try:
                    await bot.send_message(
                        admin_id,
                        f"👤 Админ @{message.from_user.username} разбанил пользователя {user_id}"
                    )
                except:
                    pass
    else:
        await message.answer(f"⚠️ Пользователь {user_id} не найден в банах")
    
    await state.clear()

@dp.callback_query(F.data == "admin_temp_ban")
async def admin_temp_ban(callback: CallbackQuery, state: FSMContext):
    """Временный бан"""
    await callback.message.edit_text(
        "⏰ Введите ID пользователя и время через пробел\n"
        "Формат: <id> <время>\n"
        "Пример: 123456789 30m\n\n"
        "Доступные форматы времени:\n"
        "• 30m - 30 минут\n"
        "• 2h - 2 часа\n"
        "• 1d - 1 день"
    )
    await state.set_state(ReferralStates.waiting_for_temp_ban_time)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_temp_ban_time)
async def process_temp_ban(message: Message, state: FSMContext):
    """Обработка временного бана"""
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❌ Неверный формат. Используйте: <id> <время>")
        return
    
    try:
        user_id = int(parts[0])
        time_str = parts[1]
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        return
    
    # Проверка защищенного ID
    if check_protected_id(user_id):
        await message.answer(f"⚠️ ID {user_id} защищен от бана")
        await state.clear()
        return
    
    # Проверка на админа
    if is_admin(user_id):
        await message.answer("⚠️ Нельзя забанить администратора")
        await state.clear()
        return
    
    seconds = parse_time_string(time_str)
    if not seconds:
        await message.answer("❌ Неверный формат времени. Используйте: 30m, 2h, 1d")
        return
    
    ban_until = datetime.now() + timedelta(seconds=seconds)
    temp_bans[user_id] = ban_until
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            f"⏰ Вы забанены до {ban_until.strftime('%d.%m.%Y %H:%M')}"
        )
    except:
        pass
    
    time_str_formatted = format_time_delta(seconds)
    await message.answer(f"✅ Пользователь {user_id} забанен на {time_str_formatted}")
    
    # Уведомление другим админам
    for admin_id in admins:
        if admin_id != message.from_user.id:
            try:
                await bot.send_message(
                    admin_id,
                    f"👤 Админ @{message.from_user.username} "
                    f"забанил пользователя {user_id} на {time_str_formatted}"
                )
            except:
                pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_blacklist_menu")
async def admin_blacklist_menu(callback: CallbackQuery):
    """Меню ЧС"""
    await callback.message.edit_text(
        "⛔ УПРАВЛЕНИЕ ЧЕРНЫМ СПИСКОМ\n\n"
        "Выберите действие:",
        reply_markup=get_admin_blacklist_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_blacklist_add")
async def admin_blacklist_add(callback: CallbackQuery, state: FSMContext):
    """Добавление в ЧС"""
    await callback.message.edit_text(
        "⛔ Введите ID пользователя для добавления в ЧС:"
    )
    await state.set_state(ReferralStates.waiting_for_blacklist_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_blacklist_id)
async def process_blacklist_add(message: Message, state: FSMContext):
    """Обработка добавления в ЧС"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    # Проверка защищенного ID
    if check_protected_id(user_id):
        await message.answer(f"⚠️ ID {user_id} защищен от ЧС")
        await state.clear()
        return
    
    # Проверка на админа
    if is_admin(user_id):
        await message.answer("⚠️ Нельзя добавить администратора в ЧС")
        await state.clear()
        return
    
    blacklist.add(user_id)
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            "⛔ Вы добавлены в черный список."
        )
    except:
        pass
    
    await message.answer(f"✅ Пользователь {user_id} добавлен в ЧС")
    
    # Уведомление другим админам
    for admin_id in admins:
        if admin_id != message.from_user.id:
            try:
                await bot.send_message(
                    admin_id,
                    f"👤 Админ @{message.from_user.username} добавил пользователя {user_id} в ЧС"
                )
            except:
                pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_blacklist_remove")
async def admin_blacklist_remove(callback: CallbackQuery, state: FSMContext):
    """Удаление из ЧС"""
    await callback.message.edit_text(
        "✅ Введите ID пользователя для удаления из ЧС:"
    )
    await state.set_state(ReferralStates.waiting_for_unblacklist_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_unblacklist_id)
async def process_blacklist_remove(message: Message, state: FSMContext):
    """Обработка удаления из ЧС"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    if user_id in blacklist:
        blacklist.remove(user_id)
        await message.answer(f"✅ Пользователь {user_id} удален из ЧС")
        
        # Уведомление пользователя
        try:
            await bot.send_message(
                user_id,
                "✅ Вы удалены из черного списка."
            )
        except:
            pass
    else:
        await message.answer(f"⚠️ Пользователь {user_id} не найден в ЧС")
    
    await state.clear()

@dp.callback_query(F.data == "admin_whitelist_menu")
async def admin_whitelist_menu(callback: CallbackQuery):
    """Меню белого списка"""
    await callback.message.edit_text(
        "📋 УПРАВЛЕНИЕ БЕЛЫМ СПИСКОМ\n\n"
        "Выберите действие:",
        reply_markup=get_admin_whitelist_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_whitelist_add")
async def admin_whitelist_add(callback: CallbackQuery, state: FSMContext):
    """Добавление в белый список"""
    await callback.message.edit_text(
        "➕ Введите ID пользователя для добавления в белый список:"
    )
    await state.set_state(ReferralStates.waiting_for_whitelist_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_whitelist_id)
async def process_whitelist_add(message: Message, state: FSMContext):
    """Обработка добавления в белый список"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    whitelist.add(user_id)
    await message.answer(f"✅ Пользователь {user_id} добавлен в белый список")
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            "💘 Вы добавлены в белый список бота!"
        )
    except:
        pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_whitelist_remove")
async def admin_whitelist_remove(callback: CallbackQuery, state: FSMContext):
    """Удаление из белого списка"""
    await callback.message.edit_text(
        "➖ Введите ID пользователя для удаления из белого списка:"
    )
    await state.set_state(ReferralStates.waiting_for_whitelist_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_whitelist_id)
async def process_whitelist_remove(message: Message, state: FSMContext):
    """Обработка удаления из белого списка"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    if user_id in whitelist and user_id != PROTECTED_ID and user_id != ADMIN_ID:
        whitelist.remove(user_id)
        await message.answer(f"✅ Пользователь {user_id} удален из белого списка")
    else:
        await message.answer(f"⚠️ Нельзя удалить защищенный ID")
    
    await state.clear()

@dp.callback_query(F.data == "admin_whitelist_show")
async def admin_whitelist_show(callback: CallbackQuery):
    """Показать белый список"""
    text = "📋 БЕЛЫЙ СПИСОК:\n\n"
    
    for uid in sorted(whitelist):
        user_info = users_db.get(uid, {})
        username = user_info.get('username', 'нет username')
        text += f"• {uid} (@{username})\n"
    
    text += f"\nВсего: {len(whitelist)}"
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    """Статистика"""
    user_id = callback.from_user.id
    
    if not is_moderator(user_id):
        await callback.answer("⛔ Нет прав")
        return
    
    total_users = len(users_db)
    active_refs = sum(data.get('active_refs', 0) for data in users_db.values())
    blacklisted = len(blacklist)
    temp_banned = len(temp_bans)
    whitelisted = len(whitelist)
    
    # Подсчет выполненных ссылок
    links_done = sum(
        1 for data in users_db.values() 
        if data.get('link1_done', False) or data.get('link2_done', False)
    )
    
    stats_text = (
        f"📊 СТАТИСТИКА БОТА\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Выполненных рефералов: {active_refs}\n"
        f"🔗 Пользователей с выполненными ссылками: {links_done}\n"
        f"⛔ В ЧС: {blacklisted}\n"
        f"⏰ Временный бан: {temp_banned}\n"
        f"💘 В белом списке: {whitelisted}\n"
        f"👑 Администраторов: {len(admins)}\n"
        f"🛡 Модераторов: {len(moderators)}\n\n"
        f"🔧 Техработы: {'ВКЛ' if maintenance_mode else 'ВЫКЛ'}"
    )
    
    await callback.message.edit_text(stats_text, reply_markup=get_back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_maintenance_on")
async def admin_maintenance_on(callback: CallbackQuery, state: FSMContext):
    """Включение техработ через админ-панель"""
    await callback.message.edit_text(
        "🔧 Включение технических работ\n\n"
        "Введите время окончания в формате:\n"
        "• ЧЧ:ММ (сегодня) - например 23:59\n"
        "• ДД.ММ.ГГГГ ЧЧ:ММ - например 31.12.2024 23:59\n"
        "• 30m, 2h, 1d - относительное время"
    )
    await state.set_state(ReferralStates.waiting_for_maintenance_time)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_maintenance_time)
async def process_maintenance_time(message: Message, state: FSMContext):
    """Обработка времени техработ"""
    # Здесь нужно global, потому что мы ИЗМЕНЯЕМ переменные
    global maintenance_mode, maintenance_end_time, maintenance_reason
    
    time_str = message.text
    
    try:
        if ':' in time_str and '.' in time_str:
            end_time = datetime.strptime(time_str, '%d.%m.%Y %H:%M')
        elif ':' in time_str:
            hours, minutes = map(int, time_str.split(':'))
            now = datetime.now()
            end_time = datetime(now.year, now.month, now.day, hours, minutes)
            if end_time < now:
                end_time += timedelta(days=1)
        else:
            seconds = parse_time_string(time_str)
            if seconds:
                end_time = datetime.now() + timedelta(seconds=seconds)
            else:
                await message.answer("❌ Неверный формат времени")
                return
        
        await state.update_data(end_time=end_time)
        await message.answer(
            "📝 Введите причину техработ (или отправьте 'нет'):"
        )
        await state.set_state(ReferralStates.waiting_for_maintenance_reason)
        
    except ValueError:
        await message.answer("❌ Неверный формат даты")

@dp.message(ReferralStates.waiting_for_maintenance_reason)
async def process_maintenance_reason(message: Message, state: FSMContext):
    """Обработка причины техработ"""
    # Здесь нужно global, потому что мы ИЗМЕНЯЕМ переменные
    global maintenance_mode, maintenance_end_time, maintenance_reason
    
    data = await state.get_data()
    end_time = data.get('end_time')
    reason = message.text if message.text.lower() != 'нет' else ""
    
    maintenance_mode = True
    maintenance_end_time = end_time
    maintenance_reason = reason
    
    maintenance_history.append({
        'admin': message.from_user.id,
        'admin_name': message.from_user.first_name,
        'start_time': datetime.now(),
        'end_time': end_time,
        'reason': reason,
        'status': 'active'
    })
    
    await message.answer(
        f"✅ Технические работы включены до {end_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"📝 Причина: {reason if reason else 'Не указана'}"
    )
    await state.clear()

@dp.callback_query(F.data == "admin_maintenance_off")
async def admin_maintenance_off(callback: CallbackQuery):
    """Выключение техработ"""
    # Здесь нужно global, потому что мы ИЗМЕНЯЕМ переменные
    global maintenance_mode, maintenance_end_time, maintenance_reason
    
    if maintenance_history:
        maintenance_history[-1]['status'] = 'completed'
        maintenance_history[-1]['actual_end_time'] = datetime.now()
    
    maintenance_mode = False
    maintenance_end_time = None
    maintenance_reason = ""
    
    await callback.message.edit_text("✅ Технические работы выключены")
    await callback.answer()

@dp.callback_query(F.data == "admin_maintenance_history")
async def admin_maintenance_history(callback: CallbackQuery):
    """История техработ"""
    if not maintenance_history:
        await callback.message.edit_text(
            "📜 История техработ пуста",
            reply_markup=get_back_keyboard()
        )
        await callback.answer()
        return
    
    text = "📜 ИСТОРИЯ ТЕХРАБОТ:\n\n"
    
    for i, record in enumerate(reversed(maintenance_history[-10:]), 1):
        admin = record.get('admin_name', f"ID: {record['admin']}")
        start = record['start_time'].strftime('%d.%m.%Y %H:%M')
        end = record['end_time'].strftime('%d.%m.%Y %H:%M')
        status = "✅" if record.get('status') == 'completed' else "⏳"
        
        text += f"{status} {i}. С {start} до {end}\n"
        text += f"   👤 {admin}\n"
        if record.get('reason'):
            text += f"   📝 {record['reason']}\n"
        text += "\n"
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_give_moder")
async def admin_give_moder(callback: CallbackQuery, state: FSMContext):
    """Выдача прав модератора"""
    await callback.message.edit_text(
        "🛡 Введите ID пользователя для выдачи прав модератора:"
    )
    await state.set_state(ReferralStates.waiting_for_moder_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_moder_id)
async def process_give_moder(message: Message, state: FSMContext):
    """Обработка выдачи прав модератора"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    moderators.add(user_id)
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            "🛡 Вам выданы права модератора!"
        )
    except:
        pass
    
    await message.answer(f"✅ Пользователю {user_id} выданы права модератора")
    
    # Уведомление другим админам
    for admin_id in admins:
        if admin_id != message.from_user.id:
            try:
                await bot.send_message(
                    admin_id,
                    f"👤 Админ @{message.from_user.username} выдал права модератора пользователю {user_id}"
                )
            except:
                pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_give_admin")
async def admin_give_admin(callback: CallbackQuery, state: FSMContext):
    """Выдача прав администратора"""
    await callback.message.edit_text(
        "👑 Введите ID пользователя для выдачи прав администратора:"
    )
    await state.set_state(ReferralStates.waiting_for_admin_id)
    await callback.answer()

@dp.message(ReferralStates.waiting_for_admin_id)
async def process_give_admin(message: Message, state: FSMContext):
    """Обработка выдачи прав администратора"""
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Неверный формат ID")
        await state.clear()
        return
    
    admins.add(user_id)
    whitelist.add(user_id)
    
    # Уведомление пользователя
    try:
        await bot.send_message(
            user_id,
            "👑 Вам выданы права администратора!"
        )
    except:
        pass
    
    await message.answer(f"✅ Пользователю {user_id} выданы права администратора")
    
    # Уведомление другим админам
    for admin_id in admins:
        if admin_id != message.from_user.id:
            try:
                await bot.send_message(
                    admin_id,
                    f"👤 Админ @{message.from_user.username} выдал права администратора пользователю {user_id}"
                )
            except:
                pass
    
    await state.clear()

# ==================== ОБРАБОТКА НЕВАЛИДНЫХ СООБЩЕНИЙ ====================

@dp.message()
async def handle_invalid_message(message: Message, state: FSMContext):
    """Обработка всех остальных сообщений"""
    user_id = message.from_user.id
    
    # Проверка защищенного ID
    check_protected_id(user_id)
    
    # Проверка бана
    if is_banned(user_id):
        return
    
    current_state = await state.get_state()
    
    if current_state:
        await message.answer(
            "⚠️ Пожалуйста, используйте кнопки для навигации.",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await message.answer(
            "🤖 Используйте команду /start для начала работы.",
            reply_markup=get_main_keyboard(user_id)
        )

# ==================== ЗАПУСК БОТА ====================

async def main():
    """Запуск бота"""
    logger.info("🚀 Запуск бота...")
    logger.info(f"👑 Главный администратор: {ADMIN_ID}")
    logger.info(f"🛡 Защищенный ID: {PROTECTED_ID}")
    logger.info(f"🔧 Техработы: {'ВКЛ' if maintenance_mode else 'ВЫКЛ'}")
    
    # Запуск обработчика консольных команд
    asyncio.create_task(console_command_handler())
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())