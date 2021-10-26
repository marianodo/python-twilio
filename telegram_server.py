#!/usr/bin/env python
# pylint: disable=C0116,W0613
# This program is dedicated to the public domain under the CC0 license.

"""
First, a few callback functions are defined. Then, those functions are passed to
the Dispatcher and registered at their respective places.
Then, the bot is started and runs until we press Ctrl-C on the command line.
Usage:
Example of a bot-user conversation using ConversationHandler.
Send /start to initiate the conversation.
Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""

import logging
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, KeyboardButton
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

from dbSigesmen import Database
import json
DATABASE_FILE = "dbConf.json"
with open(DATABASE_FILE) as db:
    configFile = json.load(db)
db = Database(configFile["user"], configFile["passwd"], configFile["host"], configFile["port"], configFile["database"])
TOKEN = configFile["telegram_token"]

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)

PHONE= range(1)

def start(update: Update, context: CallbackContext) -> int:
    """Starts the conversation and asks the user about their gender."""
    reply_keyboard = [[KeyboardButton("Mi contacto", request_contact=True)]]

    update.message.reply_text(
        'Hola. Soy el Bot de Integralcom. Necesito que me compartas tu teléfono para poder enviarte los mensajes. Gracias',
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True
        ),
    )

    return PHONE


def phone(update: Update, context: CallbackContext) -> int:
    """Stores the selected gender and asks for a photo."""

    user = update.message.from_user
    chat_id = update.message.chat.id
    phone = update.effective_message.contact.phone_number
    print(phone)
    if chat_id and phone:
        value = db.insert_chat_id(phone, chat_id)
        print(value)
        text = 'Gracias. Estás registrado para recibir nuestras notificaciones'
    else:
        text = "Ocurrió un problema. No pudimos registart su contacto. Intente nuevamente"
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardRemove(),
    )

    return ConversationHandler.END


def cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    update.message.reply_text(
        'Bye! I hope we can talk again some day.', reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END


def main() -> None:
    """Run the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(TOKEN)

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # Add conversation handler with the states GENDER, PHOTO, LOCATION and BIO
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(Filters.contact, phone)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(conv_handler)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()