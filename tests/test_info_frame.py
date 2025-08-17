import os
import tkinter as tk
import pytest

from components.info_frame import InfoFrame


def _dummy():
    pass


@pytest.mark.skipif(os.environ.get("DISPLAY", "") == "", reason="requires display")
def test_logging_and_controls():
    root = tk.Tk()
    root.withdraw()

    var = tk.IntVar()
    frame = InfoFrame(root, var, _dummy, _dummy, _dummy, _dummy)

    frame.append_llm_log(
        "request", "hola,,, , ,", label="Variaciones Iniciales"
    )
    frame.append_llm_log(
        "response", "respuesta,,, , , [], [], []"
    )
    frame.append_llm_log("request", "adhoc,,, , ,", label=None)
    frame._process_log_queue()

    text = frame.txt_logs.get("1.0", "end")
    assert "Prompt: Variaciones Iniciales | Resp: respuesta" in text
    assert ", , ," not in text
    assert "[], [], []" not in text
    assert "adhoc" not in text

    frame.toggle_pause()
    frame.append_llm_log("response", "otra")
    frame._process_log_queue()
    assert text == frame.txt_logs.get("1.0", "end")

    frame.toggle_pause()
    frame._process_log_queue()
    frame.append_llm_log("response", "nuevo")
    frame._process_log_queue()
    logs = frame.txt_logs.get("1.0", "end")
    assert "Prompt: adhoc | Resp: otra" in logs
    assert "Resp: nuevo" in logs

    frame.clear_logs()
    assert frame.txt_logs.get("1.0", "end").strip() == ""

    root.destroy()


@pytest.mark.skipif(os.environ.get("DISPLAY", "") == "", reason="requires display")
def test_truncation():
    root = tk.Tk()
    root.withdraw()

    var = tk.IntVar()
    frame = InfoFrame(root, var, _dummy, _dummy, _dummy, _dummy)

    long_prompt = "p" * 100
    long_resp = "r" * 200

    frame.append_llm_log("request", long_prompt)
    frame.append_llm_log("response", long_resp)
    frame._process_log_queue()

    text = frame.txt_logs.get("1.0", "end").strip()
    assert len(text) == len("Prompt: ") + 80 + len(" | Resp: ") + 120
    assert "p" * 80 in text
    assert "r" * 120 in text

    root.destroy()

