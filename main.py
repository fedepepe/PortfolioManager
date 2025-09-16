import uvicorn
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import RedirectResponse

from dashboard.dash_main import app

# Define the FastAPI server
server = FastAPI()
# Mount the Dash app as a sub-application in the FastAPI server
server.mount("/portfolio_manager", WSGIMiddleware(app.server))


# Define the main API endpoint
@server.get("/")
def index():
    return RedirectResponse(url="/portfolio_manager")


# Start the FastAPI server
if __name__ == "__main__":
    uvicorn.run(server, host="127.0.0.1")
