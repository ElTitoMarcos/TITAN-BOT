import os
import json
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))

@pytest.mark.skipif(os.environ.get("DISPLAY", "") == "", reason="requires display")
def test_controls_locked_without_keys():
    keys_file = os.path.join(ROOT, ".api_keys.json")
    state_path = os.path.join(ROOT, "state", "state.json")
    try:
        if os.path.exists(keys_file):
            os.remove(keys_file)
    except FileNotFoundError:
        pass
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump({"apis_verified": {"binance": True, "llm": True}}, fh)

    from ui_app import App

    app = App()
    app.update()
    app.update()

    assert not app.mass_state.apis_verified["binance"]
    assert not app.mass_state.apis_verified["llm"]
    assert app.testeos_frame.btn_toggle.cget("state") == "disabled"

    app.destroy()
    if os.path.exists(state_path):
        os.remove(state_path)
