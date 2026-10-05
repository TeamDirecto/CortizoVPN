from flask import Flask, jsonify

from app.config import load_config
from app.services.user_groups import get_user_groups

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


@app.route("/user-groups", methods=["GET"])
def user_groups():
    try:
        config = load_config()
        groups = get_user_groups(config)
        return jsonify(
            {
                "count": len(groups),
                "items": groups,
                "source": "master",
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
