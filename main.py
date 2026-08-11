"""Compatibility entrypoint for ``uvicorn main:app``."""

import uvicorn
from murmur.api import create_application
from murmur.core.config import config

app = create_application()
runtime = app.state.runtime


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=True)
