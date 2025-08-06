# shopping_bot/routes/onboarding_flow.py
from flask import Blueprint, request, jsonify, current_app
import logging

bp = Blueprint("onboarding_flow", __name__)
log = logging.getLogger(__name__)

INITIAL_DATA = {
    "societies": [
        {"id": "amrapali_sapphire", "title": "Amrapali Sapphire"},
        {"id": "parsvnath_prestige", "title": "Parsvnath Prestige"},
        {"id": "other", "title": "Other"},
    ],
    "show_custom_society": False,
    "genders": [
        {"id": "male", "title": "Male"},
        {"id": "female", "title": "Female"},
        {"id": "other", "title": "Other"},
        {"id": "prefer_not_to_say", "title": "Prefer not to say"},
    ],
    "age_groups": [
        {"id": "18_24", "title": "18-24 years"},
        {"id": "25_34", "title": "25-34 years"},
        {"id": "35_44", "title": "35-44 years"},
        {"id": "45_54", "title": "45-54 years"},
        {"id": "55_64", "title": "55-64 years"},
        {"id": "65_plus", "title": "65+ years"},
    ],
}

@bp.post("/flow/onboarding")
def onboarding_flow():
    payload = request.get_json(force=True)
    action = payload.get("action")
    if action == "init":
        # Send dropdown data back to the Flow
        return jsonify({"data": INITIAL_DATA}), 200
    if action == "validate":
        data = payload.get("payload", {})
        errors = {}
        if not data.get("society"):
            errors["society"] = "Please select your society."
        elif data["society"] == "other" and not data.get("custom_society", "").strip():
            errors["custom_society"] = "Please enter your society name."
        if not data.get("gender"):
            errors["gender"] = "Please select your gender."
        if not data.get("age_group"):
            errors["age_group"] = "Please select your age group."
        return jsonify({"errors": errors}) if errors else jsonify({}), 200
    if action == "submit":
        # Persist the user’s selections.  This example just logs them.
        log.info("Onboarding submission: %s", payload.get("payload"))
        return "", 204
    return jsonify({"error": "Unsupported action"}), 400
