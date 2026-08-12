"""Load .env before anything else imports.

`db.py` reads DATABASE_URL and `llm_client.py` reads GEMINI_API_KEY at import
time, and both are pulled in by the routers. Calling load_dotenv() inside
main.py runs it too late — the engine is already built against the default URL
and the app quietly talks to the wrong database. The package __init__ is the
only place guaranteed to run first.
"""

from dotenv import load_dotenv

load_dotenv()
