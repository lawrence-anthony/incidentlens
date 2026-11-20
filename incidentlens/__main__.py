import logging
import os

import uvicorn
from dotenv import load_dotenv

from .app import create_app
from .config import production_dependencies

load_dotenv()
logging.basicConfig(level=logging.INFO)
app = create_app(**production_dependencies())

if __name__ == "__main__":
    # Without PORT only this computer can reach the app.
    uvicorn.run(app, host="0.0.0.0" if "PORT" in os.environ else "127.0.0.1", port=int(os.environ.get("PORT", 8000)))
