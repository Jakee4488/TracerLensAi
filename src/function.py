from a2wsgi import ASGIMiddleware
from src.main import app

# This WSGI application can be directly served by functions-framework
# or any other WSGI server (like gunicorn).
proxy_app = ASGIMiddleware(app)
