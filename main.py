import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import RedirectResponse

from dashboard.dash_main import app
from database.table_definitions import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # start-up: make sure the database and its tables exist
    init_db()
    yield


# Define the FastAPI server
server = FastAPI(lifespan=lifespan)
# Mount the Dash app as a sub-application in the FastAPI server
server.mount('/portfolio_manager', WSGIMiddleware(app.server))


# Define the main API endpoint
@server.get('/')
def index():
    return RedirectResponse(url='/portfolio_manager')


# Start the FastAPI server
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    uvicorn.run(server, host='127.0.0.1')
