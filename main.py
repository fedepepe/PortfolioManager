import uvicorn
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import RedirectResponse

from dash_main import dash

# Define the FastAPI server
app = FastAPI()
# Mount the Dash app as a sub-application in the FastAPI server
app.mount("/portfolio_manager", WSGIMiddleware(dash.server))


# Define the main API endpoint
@app.get("/")
def index():
    return RedirectResponse(url="/portfolio_manager")


# Start the FastAPI server
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1")
