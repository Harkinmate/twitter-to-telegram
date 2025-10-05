import os
import json
import asyncio
import logging
from typing import List
import snscrape.modules.twitter as sntwitter
from telegram import Bot, InputMediaPhoto, InputMediaVideo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import aiohttp

# --------------- Config ---------------
CONFIG_FILE = 'config.json'
CACHE_FILE = 'cache.json'

BOT_TOKEN = os.environ.get('BOT_TOKEN')  # Set this in Render as a secret
DEFAULT_CHANNEL = os.environ.get('DEFAULT_CHANNEL')  # optional, e.g. @livescoreit

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN env var is required')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------- JSON helpers ---------------
def load_json(path, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

config = load_json(CONFIG_FILE, {
    "twitter_accounts": [],
    "interval_minutes": 3,
    "telegram_channel": DEFAULT_CHANNEL or None,
    "paused": False
})
cache = load_json(CACHE_FILE, {})  # {username: last_tweet_id}

bot = Bot(BOT_TOKEN)

# --------------- Telegram helpers ---------------
async def send_text(channel, text):
    try:
        bot.send_message(chat_id=channel, text=text, disable_web_page_preview=True)
    except Exception as e:
        logger.exception("Failed to send text: %s", e)

async def send_media(channel, media_urls: List[dict], caption: str = ""):
    """
    media_urls: list of dicts: {"type":"photo" or "video", "url": "..."}
    """
    try:
        if not media_urls:
            if caption:
                await send_text(channel, caption)
            return
        # Group photos together, videos individually after photos
        photos = [m['url'] for m in media_urls if m['type']=='photo']
        videos = [m['url'] for m in media_urls if m['type']=='video']
        if photos:
            # send as media group if multiple, else send_photo
            if len(photos) == 1:
                bot.send_photo(chat_id=channel, photo=photos[0], caption=caption)
            else:
                media_group = [InputMediaPhoto(p) for p in photos]
                # telegram bot api doesn't support caption on media_group except first item
                media_group[0].caption = caption
                bot.send_media_group(chat_id=channel, media=media_group)
        if videos:
            # send videos separately (Telegram may take time for large videos)
            for v in videos:
                bot.send_video(chat_id=channel, video=v)
        # if no photos/videos, fall back to text
        if not photos and not videos and caption:
            await send_text(channel, caption)
    except Exception as e:
        logger.exception("Failed to send media: %s", e)

async def send_to_channel_text_only(text: str):
    channel = config.get("telegram_channel")
    if not channel:
        logger.warning("Channel not set. Skipping message.")
        return
    await send_text(channel, text)

# --------------- Scraper ---------------
def get_latest_tweet_sync(username: str):
    uname = username.lstrip('@')
    try:
        for tweet in sntwitter.TwitterUserScraper(uname).get_items():
            return tweet
    except Exception as e:
        logger.exception("Scrape error for %s: %s", username, e)
    return None

def extract_media_from_tweet(tweet) -> List[dict]:
    """
    Attempt to extract media URLs from snscrape tweet object.
    Returns list of {"type":"photo"|"video", "url": "..."}
    """
    media_list = []
    # snscrape tweet may have attribute 'media'
    if getattr(tweet, "media", None):
        for m in tweet.media:
            # m may have .fullUrl or .previewUrl or .url
            url = None
            mtype = "photo"
            if hasattr(m, "fullUrl") and m.fullUrl:
                url = m.fullUrl
            elif hasattr(m, "previewUrl") and m.previewUrl:
                url = m.previewUrl
            elif hasattr(m, "url") and m.url:
                url = m.url
            # Determine type by class name or content
            if m.__class__.__name__.lower().find("video") != -1 or getattr(m, "type", "")=="video":
                mtype = "video"
            # fallback by extension
            if url:
                if any(url.lower().endswith(ext) for ext in ['.mp4', '.mov', '.webm']):
                    mtype = "video"
                media_list.append({"type": mtype, "url": url})
    # snscrape may also include 'extended_entities' or other fields, but we'll keep this simple
    return media_list

async def check_for_new_tweets():
    while True:
        try:
            if config.get("paused"):
                logger.info("Crawler paused")
            else:
                accounts = list(dict.fromkeys(config.get("twitter_accounts", [])))
                logger.info("Checking %d accounts", len(accounts))
                for acc in accounts:
                    tweet = await asyncio.to_thread(get_latest_tweet_sync, acc)
                    if not tweet:
                        continue
                    tid = str(getattr(tweet, "id", None))
                    last = cache.get(acc)
                    if last != tid:
                        # new tweet
                        text = getattr(tweet, "content", "").strip()
                        # prepare caption (text only) - no link
                        caption = f"🧵 New tweet from {acc}:\n\n{text}" if text else f"🧵 New tweet from {acc}"
                        media = extract_media_from_tweet(tweet)
                        channel = config.get("telegram_channel")
                        if media:
                            await send_media(channel, media, caption=caption)
                        else:
                            await send_text(channel, caption)
                        cache[acc] = tid
                        save_json(CACHE_FILE, cache)
        except Exception as e:
            logger.exception("Error in check loop: %s", e)
        await asyncio.sleep(max(30, config.get("interval_minutes", 3) * 60))

# --------------- Telegram command handlers ---------------
from telegram import Update

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I control the Twitter → Telegram crawler. Use /help for commands.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "/add @username - Start watching an account\n"
        "/remove @username - Stop watching\n"
        "/list - Show watched accounts\n"
        "/setchannel @channel - Set output channel\n"
        "/setinterval N - Set minutes between checks\n"
        "/pause - Pause crawling\n"
        "/resume - Resume crawling\n"
        "/status - Show current status\n"
    )
    await update.message.reply_text(txt)

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /add @username")
        return
    acc = context.args[0]
    if not acc.startswith("@"):
        acc = "@" + acc
    if acc in config["twitter_accounts"]:
        await update.message.reply_text(f"{acc} already tracked")
        return
    config["twitter_accounts"].append(acc)
    save_json(CONFIG_FILE, config)
    await update.message.reply_text(f"✅ Added {acc}")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /remove @username")
        return
    acc = context.args[0]
    if not acc.startswith("@"):
        acc = "@" + acc
    try:
        config["twitter_accounts"].remove(acc)
        save_json(CONFIG_FILE, config)
        await update.message.reply_text(f"✅ Removed {acc}")
    except ValueError:
        await update.message.reply_text(f"{acc} not found")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arr = config.get("twitter_accounts", [])
    if not arr:
        await update.message.reply_text("No accounts tracked yet.")
        return
    txt = "Tracked accounts:\n" + "\n".join(f"{i+1}. {a}" for i,a in enumerate(arr))
    await update.message.reply_text(txt)

async def cmd_setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setchannel @channel")
        return
    ch = context.args[0]
    config["telegram_channel"] = ch
    save_json(CONFIG_FILE, config)
    await update.message.reply_text(f"✅ Channel set to {ch}")

async def cmd_setinterval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setinterval N (minutes)")
        return
    try:
        n = int(context.args[0])
        config["interval_minutes"] = max(1, n)
        save_json(CONFIG_FILE, config)
        await update.message.reply_text(f"✅ Interval set to {n} minutes")
    except ValueError:
        await update.message.reply_text("Invalid number")

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config["paused"] = True
    save_json(CONFIG_FILE, config)
    await update.message.reply_text("⏸️ Crawling paused")

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config["paused"] = False
    save_json(CONFIG_FILE, config)
    await update.message.reply_text("▶️ Crawling resumed")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        f"Accounts: {len(config.get('twitter_accounts', []))}\n"
        f"Interval (min): {config.get('interval_minutes')}\n"
        f"Channel: {config.get('telegram_channel')}\n"
        f"Paused: {config.get('paused')}"
    )
    await update.message.reply_text(txt)

# --------------- App startup ---------------
async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("setchannel", cmd_setchannel))
    application.add_handler(CommandHandler("setinterval", cmd_setinterval))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))

    await application.initialize()
    await application.start()
    logger.info("Telegram bot started")
    asyncio.create_task(check_for_new_tweets())
    await application.updater.start_polling()
    await application.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
