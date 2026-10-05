from flask import Flask, jsonify

from app.config import load_config

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/nodes", methods=["GET"])
def nodes():
    config = load_config()
    return jsonify(
        {
            "dialers": config.raw.get("dialers", {}),
            "database": config.raw.get("database", {}),
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
