/**
 * The Loudrr Telegram bot: @Loudrrbot (t.me/Loudrrbot). The backend's bot
 * token belongs to this bot and Telegram signs mini-app logins with it, so
 * every link that opens the mini-app must point here. A link to any other
 * bot opens someone else's account, not Loudrr.
 */
export const BOT_USERNAME = 'Loudrrbot';
export const BOT_URL = `https://t.me/${BOT_USERNAME}`;
/** Opens the "app" Mini App; add ?startapp=<param> to pass a start param. */
export const BOT_APP_URL = `${BOT_URL}/app`;
