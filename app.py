"""
FinanceControl — main entry point.
Starts Flask in a background thread, then opens a PyWebView native window.
Run: python app.py
"""
import threading
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, send_from_directory

from api.db import init_db, seed_db
from api.lancamentos import bp as bp_lancamentos
from api.catalogo import bp as bp_catalogo
from api.importacao import bp as bp_importacao
from api.email_busca import bp as bp_email_busca

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend', 'dist')

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
app.config['JSON_SORT_KEYS'] = False


# ── static frontend ──────────────────────────────────────────────────────────
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    full = os.path.join(FRONTEND_DIR, path)
    if path and os.path.exists(full):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, 'index.html')


# ── blueprints ────────────────────────────────────────────────────────────────
app.register_blueprint(bp_lancamentos, url_prefix='/api')
app.register_blueprint(bp_catalogo,    url_prefix='/api')
app.register_blueprint(bp_importacao,  url_prefix='/api')
app.register_blueprint(bp_email_busca, url_prefix='/api')


def run_flask():
    # threaded=True: o login de email (device code flow) fica bloqueado
    # esperando o usuário confirmar no navegador — sem isso, essa espera
    # travaria toda a UI do app enquanto durasse.
    app.run(host='127.0.0.1', port=5173, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    # Init DB + seed
    init_db()
    seed_db()

    # Dev mode: just run Flask (no window) when --api flag is passed
    if '--api' in sys.argv:
        print('[FinanceControl] API running at http://127.0.0.1:5173')
        run_flask()
    else:
        # Start Flask in background thread, then open PyWebView window
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        import webview  # import here so --api mode works without pywebview
        window = webview.create_window(
            title='FinanceControl',
            url='http://127.0.0.1:5173',
            width=1280,
            height=820,
            min_size=(960, 600),
        )
        webview.start(debug=False)
