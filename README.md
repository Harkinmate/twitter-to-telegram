# Twitter → Telegram Crawler (Render-ready)

**Configured for:** Channel `@livescoreit` — text + media (no links)

## Quick start (Render)
1. Upload the contents of this repo to GitHub or upload the ZIP directly to Render.
2. In Render, create a new **Web Service** and connect/upload this repo/ZIP.
3. Set the following environment variable in Render:
   - `BOT_TOKEN` = <your-telegram-bot-token>
   - (Optional) `DEFAULT_CHANNEL` = @livescoreit
4. Start the service. The bot will begin polling and the crawler will run in the background.

## Commands (use in Telegram chat with your bot)
- `/add @username` — start watching
- `/remove @username` — stop watching
- `/list` — show tracked accounts
- `/setchannel @channel` — change output channel
- `/setinterval N` — set minutes between checks
- `/pause` — pause crawling
- `/resume` — resume crawling
- `/status` — show status

## Notes
- Do **not** commit `BOT_TOKEN` to any public repo. Use Render secrets.
- If you want link URLs included later, update `main.py` where caption is built.
