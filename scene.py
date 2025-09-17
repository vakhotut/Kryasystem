# scene.py
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Состояния разговора
class Form(StatesGroup):
    captcha = State()
    language = State()
    main_menu = State()
    city = State()
    category = State()
    district = State()
    delivery = State()
    confirmation = State()
    crypto_currency = State()
    payment = State()
    balance = State()
    balance_menu = State()
    topup_currency = State()
    topup_amount = State()
    order_history = State()
    deposit_address = State()
    invoice_check = State()

# Текстовые константы
TEXTS = {
    'ru': {
        'welcome': 'Добро пожаловать!',
        'language_selected': 'Язык выбран.',
        'captcha_enter': 'Введите код с картинки:',
        'captcha_success': 'Капча пройдена успешно!',
        'captcha_failed': 'Неверный код. Попробуйте еще раз.',
        'main_menu': '👤 Имя: {name}\n👤 Юзернейм: @{username}\n🛒 Кол-во покупок: {purchases}\n🎁 Скидка: {discount}%\n💰 Баланс: ${balance}',
        'main_menu_description': 'Добро пожаловать в наш магазин!',
        'select_category': 'Выберите категорию:',
        'select_district': 'Выберите район:',
        'select_delivery': 'Выберите тип доставки:',
        'order_summary': 'Заказ:\nТовар: {product}\nЦена: ${price}\nРайон: {district}\nДоставка: {delivery_type}',
        'select_crypto': 'Выберите способ оплаты:',
        'balance_instructions': 'Ваш баланс: ${balance}\n\nВыберите действие:',
        'balance_topup_info': 'Выберите валюту для пополнения:',
        'enter_topup_amount': 'Введите сумму в USD для пополнения баланса:',
        'invalid_amount': 'Пожалуйста, введите корректную сумму (число больше 0):',
        'order_confirmation': 'Подтвердите заказ:\n\nТовар: {product}\nЦена: ${price}\nСкидка: {discount}%\nИтоговая цена: ${final_price}\nРайон: {district}\nТип доставки: {delivery_type}\n\nВсе верно?',
        'active_invoice': 'Для пополнения баланса отправьте {crypto_amount} {crypto} на адрес:\n\n`{crypto_address}`\n\nСумма к получению: ${amount}\nДействует до: {expires_time}\nОсталось: {time_left}',
        'purchase_invoice': 'Для оплаты заказа отправьте {crypto_amount} {crypto} на адрес:\n\n`{crypto_address}`\n\nТовар: {product}\nСумма: ${amount}\nДействует до: {expires_time}\nОсталось: {time_left}',
        'invoice_time_left': 'Осталось {time_left} для оплаты заказа.',
        'balance_invoice_time_left': 'Осталось {time_left} для пополнения баланса.',
        'invoice_expired': 'Время оплаты истекло. Неудачных попыток: {failed_count}',
        'almost_banned': 'Внимание! У вас {remaining} попытка(и) перед блокировкой.',
        'ban_message': 'Вы заблокированы за неудачные попытки оплаты.',
        'balance_add_success': 'Баланс пополнен на ${amount}. Текущий баланс: ${balance}',
        'product_out_of_stock': 'Товар закончился.',
        'error': 'Произошла ошибка. Попробуйте позже.',
        'no_orders': 'У вас нет заказов.',
        'bonuses': 'Бонусная система:\n- За каждого приглашенного друга: 5% кэшбэк\n- Накопительная скидка до 15%',
        'only_ltc_supported': 'В настоящее время поддерживается только LTC',
    },
    'en': {
        'welcome': 'Welcome!',
        'language_selected': 'Language selected.',
        'captcha_enter': 'Enter the code from the image:',
        'captcha_success': 'Captcha passed successfully!',
        'captcha_failed': 'Invalid code. Try again.',
        'main_menu': '👤 Name: {name}\n👤 Username: @{username}\n🛒 Purchases: {purchases}\n🎁 Discount: {discount}%\n💰 Balance: ${balance}',
        'main_menu_description': 'Welcome to our store!',
        'select_category': 'Select category:',
        'select_district': 'Select district:',
        'select_delivery': 'Select delivery type:',
        'order_summary': 'Order:\nProduct: {product}\nPrice: ${price}\nDistrict: {district}\nDelivery: {delivery_type}',
        'select_crypto': 'Select payment method:',
        'balance_instructions': 'Your balance: ${balance}\n\nSelect action:',
        'balance_topup_info': 'Select currency for top-up:',
        'enter_topup_amount': 'Enter amount in USD to top up balance:',
        'invalid_amount': 'Please enter a valid amount (number greater than 0):',
        'order_confirmation': 'Confirm order:\n\nProduct: {product}\nPrice: ${price}\nDiscount: {discount}%\nFinal price: ${final_price}\nDistrict: {district}\nDelivery type: {delivery_type}\n\nIs everything correct?',
        'active_invoice': 'To top up balance, send {crypto_amount} {crypto} to address:\n\n`{crypto_address}`\n\nAmount to receive: ${amount}\nValid until: {expires_time}\nTime left: {time_left}',
        'purchase_invoice': 'To pay for order, send {crypto_amount} {crypto} to address:\n\n`{crypto_address}`\n\nProduct: {product}\nAmount: ${amount}\nValid until: {expires_time}\nTime left: {time_left}',
        'invoice_time_left': '{time_left} left to pay for order.',
        'balance_invoice_time_left': '{time_left} left to top up balance.',
        'invoice_expired': 'Payment time has expired. Failed attempts: {failed_count}',
        'almost_banned': 'Warning! You have {remaining} attempt(s) before blocking.',
        'ban_message': 'You are blocked for failed payment attempts.',
        'balance_add_success': 'Balance topped up by ${amount}. Current balance: ${balance}',
        'product_out_of_stock': 'Product is out of stock.',
        'error': 'An error occurred. Please try again later.',
        'no_orders': 'You have no orders.',
        'bonuses': 'Bonus system:\n- For each invited friend: 5% cashback\n- Accumulative discount up to 15%',
        'only_ltc_supported': 'Currently only LTC is supported',
    },
    'ka': {
        'welcome': 'მოგესალმებით!',
        'language_selected': 'ენა არჩეულია.',
        'captcha_enter': 'შეიყვანეთ კოდი სურათიდან:',
        'captcha_success': 'კაპჩა წარმატებით გავიდა!',
        'captcha_failed': 'არასწორი კოდი. სცადეთ თავიდან.',
        'main_menu': '👤 სახელი: {name}\n👤 მომხმარებლის სახელი: @{username}\n🛒 შენაძენების რაოდენობა: {purchases}\n🎁 ფასდაკლება: {discount}%\n💰 ბალანსი: ${balance}',
        'main_menu_description': 'კეთილი იყოს თქვენი მობრძანება ჩვენს მაღაზიაში!',
        'select_category': 'აირჩიეთ კატეგორია:',
        'select_district': 'აირჩიეთ რაიონი:',
        'select_delivery': 'აირჩიეთ მიტანის ტიპი:',
        'order_summary': 'შეკვეთა:\nპროდუქტი: {product}\nფასი: ${price}\nრაიონი: {district}\nმიტანა: {delivery_type}',
        'select_crypto': 'აირჩიეთ გადახდის მეთოდი:',
        'balance_instructions': 'თქვენი ბალანსი: ${balance}\n\nაირჩიეთ მოქმედება:',
        'balance_topup_info': 'აირჩიეთ ვალუტა ბალანსის შესავსებად:',
        'enter_topup_amount': 'შეიყვანეთ თანხა USD-ში ბალანსის შესავსებად:',
        'invalid_amount': 'გთხოვთ, შეიყვანოთ სწორი თანხა (0-ზე მეტი რიცხვი):',
        'order_confirmation': 'დაადასტურეთ შეკვეთა:\n\nპროდუქტი: {product}\nფასი: ${price}\nფასდაკლება: {discount}%\nსაბოლოო ფასი: ${final_price}\nრაიონი: {district}\nმიტანის ტიპი: {delivery_type}\n\nყველაფერი სწორია?',
        'active_invoice': 'ბალანსის შესავსებად, გადაირიცხეთ {crypto_amount} {crypto} მისამართზე:\n\n`{crypto_address}`\n\nმიღებული თანხა: ${amount}\nმოქმედებს: {expires_time}\nდარჩენილია: {time_left}',
        'purchase_invoice': 'შეკვეთის გადასახდელად, გადაირიცხეთ {crypto_amount} {crypto} მისამართზე:\n\n`{crypto_address}`\n\nპროდუქტი: {product}\nთანხა: ${amount}\nმოქმედებს: {expires_time}\nდარჩენილია: {time_left}',
        'invoice_time_left': 'შეკვეთის გადასახდელად დარჩა {time_left}.',
        'balance_invoice_time_left': 'ბალანსის შესავსებად დარჩა {time_left}.',
        'invoice_expired': 'გადახდის დრო ამოიწურა. წარუმატებელი მცდელობები: {failed_count}',
        'almost_banned': 'ყურადღება! თქვენ გაქვთ {remaining} მცდელობა დაბლოკვამდე.',
        'ban_message': 'თქვენ დაბლოკილი ხართ წარუმატებელი გადახდების მცდელობების გამო.',
        'balance_add_success': 'ბალანსი შეივსო ${amount}-ით. მიმდინარე ბალანსი: ${balance}',
        'product_out_of_stock': 'პროდუქტი ამოიწურა.',
        'error': 'მოხდა შეცდომა. გთხოვთ, სცადოთ მოგვიანებით.',
        'no_orders': 'თქვენ არ გაქვთ შეკვეთები.',
        'bonuses': 'ბონუს სისტემა:\n- ყოველი მოწვეული მეგობრისთვის: 5% cashback\n- დაგროვებითი ფასდაკლება 15%-მდე',
        'only_ltc_supported': 'Currently only LTC is supported',
    }
}

# Функции для создания клавиатур
def create_language_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="Русский", callback_data='lang_ru'),
        InlineKeyboardButton(text="English", callback_data='lang_en'),
        InlineKeyboardButton(text="ქართული", callback_data='lang_ka')
    )
    builder.adjust(1)
    return builder.as_markup()

def create_main_menu_keyboard(user_data, cities, lang):
    builder = InlineKeyboardBuilder()
    
    for city in cities:
        builder.row(InlineKeyboardButton(text=city['name'], callback_data=f"city_{city['name']}"))
    
    builder.row(
        InlineKeyboardButton(text=f"💰 {get_text(lang, 'balance', balance=user_data['balance'] or 0)}", callback_data="balance"),
        InlineKeyboardButton(text="📦 История заказов", callback_data="order_history")
    )
    
    builder.row(
        InlineKeyboardButton(text="🎁 Бонусы", callback_data="bonuses"),
        InlineKeyboardButton(text="📚 Правила", url=get_bot_setting('rules_link'))
    )
    builder.row(
        InlineKeyboardButton(text="👨‍💻 Оператор", url=get_bot_setting('operator_link')),
        InlineKeyboardButton(text="🔧 Техподдержка", url=get_bot_setting('support_link'))
    )
    builder.row(InlineKeyboardButton(text="📢 Наш канал", url=get_bot_setting('channel_link')))
    builder.row(InlineKeyboardButton(text="⭐ Отзывы", url=get_bot_setting('reviews_link')))
    builder.row(InlineKeyboardButton(text="🌐 Наш сайт", url=get_bot_setting('website_link')))
    builder.row(InlineKeyboardButton(text="🌐 Смена языка", callback_data="change_language"))
    
    return builder.as_markup()

def create_balance_menu_keyboard(lang):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="topup_balance"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    return builder.as_markup()

def create_topup_currency_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="LTC", callback_data="topup_ltc"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_balance_menu"))
    return builder.as_markup()

def create_category_keyboard(categories):
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.row(InlineKeyboardButton(text=category['name'], callback_data=f"cat_{category['name']}"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    return builder.as_markup()

def create_products_keyboard(products):
    builder = InlineKeyboardBuilder()
    for product_name, product_info in products.items():
        price = product_info['price']
        builder.row(InlineKeyboardButton(text=f"{product_name} - ${price}", callback_data=f"prod_{product_name}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_city"))
    return builder.as_markup()

def create_districts_keyboard(districts):
    builder = InlineKeyboardBuilder()
    for district in districts:
        builder.row(InlineKeyboardButton(text=district, callback_data=f"dist_{district}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_category"))
    return builder.as_markup()

def create_delivery_types_keyboard(delivery_types):
    builder = InlineKeyboardBuilder()
    for del_type in delivery_types:
        builder.row(InlineKeyboardButton(text=del_type, callback_data=f"del_{del_type}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_district"))
    return builder.as_markup()

def create_confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes"))
    builder.row(InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_delivery"))
    return builder.as_markup()

def create_payment_keyboard(user_balance, final_price):
    builder = InlineKeyboardBuilder()
    
    if user_balance >= final_price:
        builder.row(InlineKeyboardButton(
            text=f"💰 Оплатить балансом (${user_balance})", 
            callback_data="pay_with_balance"
        ))
    
    builder.row(InlineKeyboardButton(text="LTC", callback_data="crypto_LTC"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_confirmation"))
    return builder.as_markup()

def create_invoice_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_invoice"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_invoice")
    )
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    return builder.as_markup()

def create_order_history_keyboard(orders):
    builder = InlineKeyboardBuilder()
    
    for order in orders:
        order_time = order['purchase_time'].strftime("%d.%m %H:%M")
        
        product_name = order['product']
        if len(product_name) > 15:
            product_name = product_name[:12] + "..."
        
        btn_text = f"{order_time} - {product_name} - {order['price']}$"
        
        builder.row(InlineKeyboardButton(
            text=btn_text, 
            callback_data=f"view_order_{order['id']}"
        ))
    
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    return builder.as_markup()

def create_order_details_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад к истории", callback_data="order_history"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))
    return builder.as_markup()

def create_deposit_address_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Проверить статус", callback_data="check_deposit_status"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_topup_menu"))
    return builder.as_markup()

# Вспомогательные функции
def get_text(lang, key, **kwargs):
    text = TEXTS.get(lang, {}).get(key, TEXTS['ru'].get(key, key))
    return text.format(**kwargs) if kwargs else text

def get_bot_setting(key):
    # Импортируем здесь, чтобы избежать циклических импортов
    from bot import BOT_SETTINGS
    return BOT_SETTINGS.get(key, "")
