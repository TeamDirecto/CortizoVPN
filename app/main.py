import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, render_template, request

from app.config import load_config
from app.services.user_groups import get_user_groups, build_user_group_plan, apply_user_group_plan
from app.services.users import propose_username
from app.services.extensions import build_extension_plan, verify_extension_cluster

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

ALLOWED_ORIGINS = {
    "https://teamdirecto.github.io",
}


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/nodes", methods=["GET"])
@app.route("/api/nodes", methods=["GET"])
def nodes():
    config = load_config()
    return jsonify(
        {
            "dialers": config.raw.get("dialers", {}),
            "database": config.raw.get("database", {}),
        }
    )


@app.route("/user-groups", methods=["GET"])
@app.route("/api/user-groups", methods=["GET"])
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


@app.route("/api/user-groups/plan", methods=["GET"])
def user_groups_plan():
    try:
        config = load_config()
        return jsonify(build_user_group_plan(config))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/user-groups/apply", methods=["POST"])
def user_groups_apply():
    try:
        if request.args.get("confirm") != "CREATE_USER_GROUPS":
            return jsonify({
                "error": "Confirmacion requerida: CREATE_USER_GROUPS"
            }), 400

        config = load_config()
        return jsonify(apply_user_group_plan(config))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/extensions/<extension>/plan", methods=["GET"])
def extension_plan(extension):
    try:
        config = load_config()
        return jsonify(build_extension_plan(config, extension))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/extensions/<extension>/verify", methods=["GET"])
def extension_verify(extension):
    try:
        config = load_config()
        return jsonify(verify_extension_cluster(config, extension))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/users/preview", methods=["GET"])
def user_preview():
    try:
        config = load_config()
        result = propose_username(
            config,
            request.args.get("first_name", ""),
            request.args.get("second_name", ""),
            request.args.get("paternal_surname", ""),
            request.args.get("maternal_surname", ""),
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
