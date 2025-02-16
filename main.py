import uvicorn
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import RedirectResponse

from dash_portfolio import dash as dash_pf

# Define the FastAPI server
app = FastAPI()
# Mount the Dash app as a sub-application in the FastAPI server
app.mount("/portfolio", WSGIMiddleware(dash_pf.server))


# Define the main API endpoint
@app.get("/")
def index():
    return RedirectResponse(url="/portfolio")


# Start the FastAPI server
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1")
