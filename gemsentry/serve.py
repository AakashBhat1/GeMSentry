"""WSGI server selection.

`app.run()` is Flask's development server: single-process, no request queuing
and explicitly not built for sustained use. Waitress is a pure-Python
production WSGI server that runs natively on Windows (gunicorn does not), so it
is preferred whenever installed and the dev server stays as the fallback.
"""

import logging

logger = logging.getLogger("gemsentry")


def serve(app, host, port):
    """Run ``app`` on the best available server. Blocks until shut down."""
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        logger.warning(
            "waitress is not installed -- falling back to the Flask development "
            "server, which is not built for sustained use. Install it with: "
            "uv sync  (or: pip install waitress)"
        )
        app.run(host=host, port=port, debug=False)
        return

    logger.info("Serving with waitress on %s:%s", host, port)
    waitress_serve(app, host=host, port=port, threads=8, ident="GeMSentry")
