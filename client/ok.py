"""Independently testable Flask application for client-specific routes."""

from flask import Flask, jsonify


app = Flask(__name__)


@app.get("/")
@app.get("/health")
def health():
    """Reports whether the client API is available."""
    return jsonify({"status": "ok", "service": "client"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=2051, debug=True)
