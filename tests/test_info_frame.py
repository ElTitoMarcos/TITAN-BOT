import tkinter as tk

from components.info_frame import InfoFrame


def _dummy():
    pass


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
    assert 'Envío LLM: Prompt "Variaciones Iniciales"' in text
    assert 'Respuesta LLM:' in text
    assert ', , ,' not in text
    assert '[], [], []' not in text
    assert 'Envío LLM: adhoc' in text

    frame.toggle_pause()
    frame.append_llm_log("response", "otra")
    frame._process_log_queue()
    assert text == frame.txt_logs.get("1.0", "end")

    frame.toggle_pause()
    frame.append_llm_log("response", "nuevo")
    frame._process_log_queue()
    assert 'nuevo' in frame.txt_logs.get("1.0", "end")

    frame.clear_logs()
    assert frame.txt_logs.get("1.0", "end").strip() == ""

    root.destroy()

