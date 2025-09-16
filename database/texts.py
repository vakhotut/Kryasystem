# Тексты бота
default_texts = {
    'ru': {
        'welcome': 'Добро пожаловать!',
        'captcha': 'Для входа решите каптчу: {code}\nВведите 5 цифр:',
        'captcha_enter': 'Введите 5 цифр с изображения:',
        'captcha_failed': 'Неверная каптча! Попробуйте снова:',
        'language_selected': 'Язык установлен: Русский',
        'main_menu': "👤 Имя: {name}\n📛 Юзернейм: @{username}\n🛒 Покупок: {purchases}\n🎯 Скидка: {discount}%\n💰 Баланс: {balance}$",
        'select_city': 'Выберите город:',
        'select_category': 'Выберите категорию:',
        'select_subcategory': 'Выберите подкатегорию:',
        'select_district': 'Выберите район:',
        'select_delivery': 'Выберите тип доставки:',
        'order_summary': "Информация о заказе:\n📦 Товар: {product}\n💵 Стоимость: {price}$\n🏙 Район: {district}\n🚚 Тип доставки: {delivery_type}\n\nВсё верно?",
        'select_crypto': 'Выберите криптовалюту для оплата:',
        'payment_instructions': "Оплатите {amount} {currency} на адрес:\n`{payment_address}`\n\nОтсканируйте QR-код для оплаты:\nПосле подтверждения 3 сетевых подтверждений товар будет выслан автоматически.",
        'payment_timeout': 'Время оплата истекло. Заказ отменен.',
        'payment_success': 'Оплата получена! Ваш товар:\n\n{product_image}',
        'balance': 'Ваш баланс: {balance}$',
        'balance_add': 'Введите сумму для пополнения баланса в $:',
        'balance_add_success': 'Баланс пополнен на {amount}$. Текущий баланс: {balance}$',
        'support': 'По всем вопросам обращайтесь к @support_username',
        'bonuses': 'Бонусная система:\n- За каждую 5-ю покупку скидка 10%\n- Пригласи друга и получи 50$ на баланс',
        'rules': 'Правила:\n1. Не сообщайте никому данные о заказе\n2. Оплата только в течение 60 минут\n3. При нарушении правил - бан',
        'reviews': 'Наши отзывы: @reviews_channel',
        'error': 'Произошла ошибка. Попробуйте позже.',
        'ban_message': 'Вы забанены на 24 часа из-за 3 неудачных попыток оплаты.',
        'back': '⬅️ Назад',
        'main_menu_button': '🏠 Главное меню',
        'last_order': 'Информация о последнем заказе',
        'no_orders': 'У вас еще не было заказов',
        'main_menu_description': '''Добро пожаловать в магазин!

Это телеграмм бот для быстрых покупки. 🛒 Так же есть официальный магазин Mega, нажимайте перейти и выбирайте среди огромного ассортимента! 🪏

❗️ Мы соблюдаем полную конфиденциальность наших клиентов. Мусора бляди! 🤙🏼💪''',
        'balance_instructions': '''Ваш баланс: {balance}$

Инструкция по пополнению баланса:
Русский: https://telegra.ph/RU-Kak-popolnit-balans-cherez-Litecoin-LTC-06-15
English: https://telegra.ph/EN-How-to-Top-Up-Balance-via-Litecoin-LTC-06-15
ქартули: https://telegra.ph/KA-როგორ-შევავსოთ-ბალანსი-Litecoin-ით-LTC-06-15''',
        'balance_topup_info': '''💳 Пополнение баланса

❗️ Важная информация:
• Минимальная сумма пополнения: $1
• Адрес кошелька резервируется на 30 минут
• Все пополнения на этот адрес будут зачисленны на ваш баланс
• После истечения времени адрес освобождается''',
        'active_invoice': '''💳 Активный инвойс

📝 Адрес для оплаты: `{crypto_address}`
💎 Сумма к оплате: {crypto_amount} LTC
💰 Сумма в USD: ${amount}

⏱ Действительно до: {expires_time}
❗️ Осталось времени: {time_left}

⚠️ Важно:
• Отправьте точную сумму на указанный адрес
• После 3 подтверждений сети товар будет отправлен
• При отмене или истечении времени - +1 неудачная попытка
• 3 неудачные попытки - бан на 24 часа''',
        'purchase_invoice': '''💳 Оплата заказа

📦 Товар: {product}
📝 Адрес для оплаты: `{crypto_address}`
💎 Сумма к оплате: {crypto_amount} LTC
💰 Сумма в USD: ${amount}

⏱ Действительно до: {expires_time}
❗️ Осталось времени: {time_left}

⚠️ Важно:
• Отправьте точную сумму на указанный адрес
• После 3 подтверждений сети товар будет отправлен
• При отмене или истечении времени - +1 неудачная попытка
• 3 неудачные попытки - бан на 24 часа''',
        'invoice_time_left': '⏱ До отмене инвойса осталось: {time_left}',
        'invoice_cancelled': '❌ Инвойс отменен. Неудачных попыток: {failed_count}/3',
        'invoice_expired': '⏰ Время инвойса истекло. Неудачных попыток: {failed_count}/3',
        'almost_banned': '⚠️ Внимание! После еще {remaining} неудачных попыток вы будете забанены на 24 часа!',
        'product_out_of_stock': '❌ Товар временно отсутствует',
        'product_reserved': '✅ Товар забронирован',
        'product_released': '✅ Товар возвращен в продажу',
        'deposit_confirmed': '✅ Депозит подтвержден! Зачислено: {amount_usd:.2f}$ (≈{amount_ltc} LTC)'
    },
    'en': {
        'welcome': 'Welcome!',
        'captcha': 'To enter, solve the captcha: {code}\nEnter 5 digits:',
        'captcha_enter': 'Enter 5 digits from the image:',
        'captcha_failed': 'Invalid captcha! Try again:',
        'language_selected': 'Language set: English',
        'main_menu': "👤 Name: {name}\n📛 Username: @{username}\n🛒 Purchases: {purchases}\n🎯 Discount: {discount}%\n💰 Balance: {balance}$",
        'select_city': 'Select city:',
        'select_category': 'Select category:',
        'select_subcategory': 'Select subcategory:',
        'select_district': 'Select district:',
        'select_delivery': 'Select delivery type:',
        'order_summary': "Order information:\n📦 Product: {product}\n💵 Price: {price}$\n🏙 District: {district}\n🚚 Delivery type: {delivery_type}\n\nIs everything correct?",
        'select_crypto': 'Select cryptocurrency for payment:',
        'payment_instructions': "Pay {amount} {currency} to address:\n`{payment_address}`\n\nOr scan QR-code:\nAfter 3 network confirmations, the product will be sent automatically.",
        'payment_timeout': 'Payment time has expired. Order canceled.',
        'payment_success': 'Payment received! Your product:\n\n{product_image}',
        'balance': 'Your balance: {balance}$',
        'balance_add': 'Enter the amount to top up your balance in $:',
        'balance_add_success': 'Balance topped up by {amount}$. Current balance: {balance}$',
        'support': 'For all questions contact @support_username',
        'bonuses': 'Bonus system:\n- 10% discount for every 5th purchase\n- Invite a friend and get 50$ on your balance',
        'rules': 'Rules:\n1. Do not share order information with anyone\n2. Payment only within 60 minutes\n3. Ban for breaking the rules',
        'reviews': 'Our reviews: @reviews_channel',
        'error': 'An error occurred. Please try again later.',
        'ban_message': 'You are banned for 24 hours due to 3 failed payment attempts.',
        'back': '⬅️ Back',
        'main_menu_button': '🏠 Main Menu',
        'last_order': 'Information about last order',
        'no_orders': 'You have no orders yet',
        'main_menu_description': '''Welcome to the store!

This is a telegram bot for quick purchases. 🛒 There is also an official Mega store, click go and choose from a huge assortment! 🪏

❗️ We maintain complete confidentiality of our customers. Pig cops! 🤙🏼💪''',
        'balance_instructions': '''Your balance: {balance}$

Balance top-up instructions:
Russian: https://telegra.ph/RU-Kak-popolnit-balans-cherez-Litecoin-LTC-06-15
English: https://telegra.ph/EN-How-to-Top-Up-Balance-via-Litecoin-LTC-06-15
Georgian: https://telegra.ph/KA-როგორ-შევავსოთ-ბალანსი-Litecoin-ით-LTC-06-15''',
        'balance_topup_info': '''💳 Balance top-up

❗️ Important information:
• Minimum top-up amount: $1
• Wallet address is reserved for 30 minutes
• All top-ups to this address will be credited to your balance
• After the time expires, the address is released''',
        'active_invoice': '''💳 Active Invoice

📝 Payment address: `{crypto_address}`
💎 Amount to pay: {crypto_amount} LTC
💰 Amount in USD: ${amount}

⏱ Valid until: {expires_time}
❗️ Time left: {time_left}

⚠️ Important:
• Send the exact amount to the specified address
• After 3 network confirmations the product will be sent
• On cancellation or timeout - +1 failed attempt
• 3 failed attempts - 24 hour ban''',
        'purchase_invoice': '''💳 Order Payment

📦 Product: {product}
📝 Payment address: `{crypto_address}`
💎 Amount to pay: {crypto_amount} LTC
💰 Amount in USD: ${amount}

⏱ Valid until: {expires_time}
❗️ Time left: {time_left}

⚠️ Important:
• Send the exact amount to the specified address
• After 3 network confirmations the product will be sent
• On cancellation or timeout - +1 failed attempt
• 3 failed attempts - 24 hour ban''',
        'invoice_time_left': '⏱ Time until invoice cancellation: {time_left}',
        'invoice_cancelled': '❌ Invoice cancelled. Failed attempts: {failed_count}/3',
        'invoice_expired': '⏰ Invoice expired. Failed attempts: {failed_count}/3',
        'almost_banned': '⚠️ Warning! After {remaining} more failed attempts you will be banned for 24 hours!',
        'product_out_of_stock': '❌ Product temporarily out of stock',
        'product_reserved': '✅ Product reserved',
        'product_released': '✅ Product returned to stock',
        'deposit_confirmed': '✅ Deposit confirmed! Credited: {amount_usd:.2f}$ (≈{amount_ltc} LTC)'
    },
    'ka': {
        'welcome': 'კეთილი იყოს თქვენი მობრძანება!',
        'captcha': 'შესასვლელად გადაწყვიტეთ captcha: {code}\nშეიყვანეთ 5 ციფრი:',
        'captcha_enter': 'შეიყვანეთ 5 ციფრი სურათიდან:',
        'captcha_failed': 'არასწორი captcha! სცადეთ თავიდან:',
        'language_selected': 'ენა დაყენებულია: ქართული',
        'main_menu': "👤 სახელი: {name}\n📛 მომხმარებლის სახელი: @{username}\n🛒 ყიდვები: {purchases}\n🎯 ფასდაკლება: {discount}%\n💰 ბალანსი: {balance}$",
        'select_city': 'აირჩიეთ ქალაქი:',
        'select_category': 'აირჩიეთ კატეგორია:',
        'select_subcategory': 'აირჩიეთ ქვეკატეგორია:',
        'select_district': 'აირჩიეთ რაიონი:',
        'select_delivery': 'აირჩიეთ მიწოდების ტიპი:',
        'order_summary': "შეკვეთის ინფორმაცია:\n📦 პროდუქტი: {product}\n💵 ფასი: {price}$\n🏙 რაიონი: {district}\n🚚 მიწოდების ტიპი: {delivery_type}\n\nყველაფერი სწორია?",
        'select_crypto': 'აირჩიეთ კრიპტოვალუტა გადასახდელად:',
        'payment_instructions': "გადაიხადეთ {amount} {currency} მისამართზე:\n`{payment_address}`\n\nან სკანირება QR-კოდი:\n3 ქსელური დადასტურების შემდეგ პროდუქტი გამოგეგზავნებათ ავტომატურად.",
        'payment_timeout': 'გადახდის დრო ამოიწურა. შეკვეთა გაუქმებულია.',
        'payment_success': 'გადახდა მიღებულია! თქვენი პროდუქტი:\n\n{product_image}',
        'balance': 'თქვენი ბალანსი: {balance}$',
        'balance_add': 'შეიყვანეთ ბალანსის შევსების რაოდენობა $:',
        'balance_add_success': 'ბალანსი შეივსო {amount}$-ით. მიმდინარე ბალანსი: {balance}$',
        'support': 'ყველა კითხვისთვის დაუკავშირდით @support_username',
        'bonuses': 'ბონუს სისტემა:\n- ყოველ მე-5 ყიდვაზე 10% ფასდაკლება\n- მოიწვიე მეგობარი და მიიღე 50$ ბალანსზე',
        'rules': 'წესები:\n1. არავის არ შეახოთ შეკვეთის ინფორმაცია\n2. გადახდა მხოლოდ 60 წუთის განმავლობაში\n3. წესების დარღვევაზე - ბანი',
        'reviews': 'ჩვენი მიმოხილვები: @reviews_channel',
        'error': 'მოხდა შეცდომა. სცადეთ მოგვიანებით.',
        'ban_message': '3 წარუმატებელი გადახდის მცდელობის გამო თქვენ დაბლოკილი ხართ 24 საათის განმავლობაში.',
        'back': '⬅️ უკან',
        'main_menu_button': '🏠 მთავარი მენიუ',
        'last_order': 'ბოლო შეკვეთის ინფორმაცია',
        'no_orders': 'ჯერ არ გაქვთ შეკვეთები',
        'main_menu_description': '''მაღაზიაში მოგესალმებით!

ეს არის ტელეგრამ ბოტი სწრაფი შესყიდვებისთვის. 🛒 ასევე არის ოფიციალური Mega მაღაზია, დააჭირეთ გადასვლას და აირჩიეთ უზარმაზარი ასორტიმენტიდან! 🪏

❗️ ჩვენ ვიცავთ ჩვენი კლიენტების სრულ კონფიდენციალურობას. ღორის პოლიციელები! 🤙🏼💪''',
        'balance_instructions': '''თქვენი ბალანსი: {balance}$

ბალანსის შევსების ინსტრუქცია:
Русский: https://telegra.ph/RU-Kak-popolnit-balans-cherez-Litecoin-LTC-06-15
English: https://telegra.ph/EN-How-to-Top-Up-Balance-via-Litecoin-LTC-06-15
ქართული: https://telegra.ph/KA-როგორ-შევავსოთ-ბალანსი-Litecoin-ით-LTC-06-15''',
        'balance_topup_info': '''💳 ბალანსის შევსება

❗️ მნიშვნელოვანი ინფორმაცია:
• მინიმალური შევსების რაოდენობა: $1
• საფულის მისამართი იყიდება 30 წუთის განმავლობაში
• ყველა შევსება ამ მისამართზე ჩაირიცხება თქვენს ბალანსზე
• დროის ამოწურვის შემდეგ მისამართი გათავისუფლდება''',
        'active_invoice': '''💳 აქტიური ინვოისი

📝 გადახდის მისამართი: `{crypto_address}`
💎 გადასახდელი რაოდენობა: {crypto_amount} LTC
💰 რაოდენობა USD-ში: ${amount}

⏱ მოქმედებს: {expires_time}
❗️ დარჩენილი დრო: {time_left}

⚠️ მნიშვნელოვანი:
• გადაიხადეთ ზუსტი რაოდენობა მითითებულ მისამართზე
• 3 ქსელური დადასტურების შემდეგ პროდუქტი გაიგზავნება
• გაუქმების ან დროის ამოწურვის შემთხვევაში - +1 წარუმატებელი მცდელობა
• 3 წარუ�მატებელი მცდელობა - 24 საათიანი ბანი''',
        'purchase_invoice': '''💳 შეკვეთის გადახდა

📦 პროდუქტი: {product}
📝 გადახდის მისამართი: `{crypto_address}`
💎 გადასახდელი რაოდენობა: {crypto_amount} LTC
💰 რაოდენობა USD-ში: ${amount}

⏱ მოქმედებს: {expires_time}
❗️ დარჩენილი დრო: {time_left}

⚠️ მნიშვნელოვანი:
• გადაიხადეთ ზუსტი რაოდენობა მითითებულ მისამართზე
• 3 ქსელური დადასტურების შემდეგ პროდუქტი გაიგზავნება
• გაუქმების ან დროის ამოწურვის შემთხვევაში - +1 წარუმატებელი მცდელობა
• 3 წარუმატებელი მცდელობა - 24 საათიანი ბანი''',
        'invoice_time_left': '⏱ ინვოისის გაუქმებამდე დარჩა: {time_left}',
        'invoice_cancelled': '❌ ინვოისი გაუქმებულია. წარუმატებელი მცდელობები: {failed_count}/3',
        'invoice_expired': '⏰ ინვოისის დრო ამოიწურა. წარუმატებელი მცდელობები: {failed_count}/3',
        'almost_banned': '⚠️ გაფრთხილება! კიდევ {remaining} წარუმატებელი მცდელობის შემდეგ დაბლოკილი იქნებით 24 საათის განმავლობაში!',
        'product_out_of_stock': '❌ პროდუქტი დროებით არ არის მარაგში',
        'product_reserved': '✅ პროდუქტი დაჯავშნულია',
        'product_released': '✅ პროდუქტი დაბრუნდა მარაგში',
        'deposit_confirmed': '✅ დეპოზიტი დადასტურებულია! ჩაირიცხა: {amount_usd:.2f}$ (≈{amount_ltc} LTC)'
    }
}

# Настройки бота по умолчанию
default_settings = {
    'main_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'balance_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'category_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'subcategory_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'district_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'delivery_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'confirmation_menu_image': "https://github.com/vakhotut/Kryasystem/blob/95692762b04dde6722f334e2051118623e67df47/IMG_20250906_162606_873.jpg?raw=true",
    'rules_link': "https://t.me/your_rules",
    'operator_link': "https://t.me/your_operator",
    'support_link': "https://t.me/your_support",
    'channel_link': "https://t.me/your_channel",
    'reviews_link': "https://t.me/your_reviews",
    'website_link': "https://yourwebsite.com",
    'personal_bot_link': "https://t.me/your_bot"
}

def get_text(lang, key, **kwargs):
    """Функция для получения текста из файла (альтернатива базе данных)"""
    try:
        if lang not in default_texts:
            return f"Language {lang} not found"
        if key not in default_texts[lang]:
            return f"Text key {key} not found for language {lang}"
        
        text = default_texts[lang][key]
        try:
            if kwargs:
                text = text.format(**kwargs)
            return text
        except KeyError as e:
            return text
    except Exception as e:
        return "Error loading text"

def get_bot_setting(key):
    """Функция для получения настройки бота из файла (альтернатива базе данных)"""
    try:
        return default_settings.get(key, "")
    except Exception as e:
        return ""
