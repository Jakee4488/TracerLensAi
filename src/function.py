import werkzeug.wrappers
from a2wsgi import ASGIMiddleware
from src.fast_api_app import app

# Convert the FastAPI (ASGI) application to WSGI.
wsgi_app = ASGIMiddleware(app)

def proxy_app(request):
    # Google Cloud Functions requires the entry point to be a function taking a request.
    # We bridge the Flask request to our WSGI app, and return a standard Response.
    return werkzeug.wrappers.Response.from_app(wsgi_app, request.environ)
