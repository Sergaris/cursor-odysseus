# launcher.py

"""Dedicated entrypoint for the standalone Windows portable launcher.



Handles:

- Immediate GUI splash screen creation using tkinter.

- Suppressing console stream crashes in windowed GUI mode via NullWriter.

- Spawning system tray icon via pystray and Pillow (lazy-loaded).

- Native desktop window (WebView2) or default browser for the UI.

- Launching the FastAPI server (importing and running app.py).

"""

import os
import sys

# Subprocess modes: python tool script or MCP server — no GUI.
try:
    from src.subprocess_entry import (
        is_mcp_worker_argv,
        is_python_worker_argv,
        run_mcp_worker_if_requested,
        run_python_worker_if_requested,
    )

    if is_python_worker_argv():
        run_python_worker_if_requested()
    elif is_mcp_worker_argv():
        run_mcp_worker_if_requested()
except Exception:
    pass

if sys.platform == "win32":
    try:
        from src.windows_subprocess import configure_windows_subprocess_runtime

        configure_windows_subprocess_runtime()
    except Exception:
        pass

import logging

import threading

import time

import webbrowser



logger = logging.getLogger(__name__)



from desktop_shell import (

    open_external_browser,

    run_desktop_app,

    should_use_desktop_shell,

)



def _enforce_single_desktop_instance() -> None:
    """One desktop app instance; second launch focuses the existing window."""
    if sys.platform != "win32":
        return
    if not getattr(sys, "frozen", False) and not should_use_desktop_shell():
        return
    from src.subprocess_entry import is_mcp_worker_argv, is_python_worker_argv
    if is_python_worker_argv() or is_mcp_worker_argv():
        return
    from src.desktop_single_instance import ensure_single_desktop_instance
    if not ensure_single_desktop_instance():
        sys.exit(0)


_enforce_single_desktop_instance()



# Define a dummy NullWriter to suppress standard stream crashes (isatty etc.) in GUI mode

class NullWriter:

    def __init__(self) -> None:

        self._sink = open(os.devnull, "w", encoding="utf-8", errors="replace")

    def write(self, text):

        pass

    def flush(self):

        pass

    def isatty(self):

        return False

    def fileno(self):

        return self._sink.fileno()



if sys.stdout is None:

    sys.stdout = NullWriter()

if sys.stderr is None:

    sys.stderr = NullWriter()





splash_root = None

_splash_thread: threading.Thread | None = None

_tray_icon = None

_server_controller = None

_last_show_ui_at = 0.0

_SHOW_UI_DEBOUNCE_SEC = 0.75





def _should_show_splash() -> bool:

    return getattr(sys, "frozen", False) or should_use_desktop_shell()





def start_splash_screen() -> None:

    """Show a lightweight splash while the backend warms up."""

    global splash_root, _splash_thread

    if not _should_show_splash() or _splash_thread is not None:

        return



    import tkinter as tk



    def show_splash_instantly() -> None:

        global splash_root

        try:

            splash_root = tk.Tk()

            splash_root.title("Odysseus")

            splash_root.overrideredirect(True)

            splash_root.configure(bg="#1a1c23")

            splash_root.config(

                highlightbackground="#e06c75",

                highlightcolor="#e06c75",

                highlightthickness=1,

            )



            w, h = 360, 160

            ws = splash_root.winfo_screenwidth()

            hs = splash_root.winfo_screenheight()

            x = (ws - w) // 2

            y = (hs - h) // 2

            splash_root.geometry(f"{w}x{h}+{x}+{y}")



            tk.Label(

                splash_root,

                text="⛵ Odysseus",

                font=("Segoe UI", 22, "bold"),

                bg="#1a1c23",

                fg="#e06c75",

            ).pack(pady=(22, 2))

            tk.Label(

                splash_root,

                text="Launching background services...",

                font=("Segoe UI", 10),

                bg="#1a1c23",

                fg="#d1d4e0",

            ).pack(pady=2)

            tk.Label(

                splash_root,

                text="Please wait, this will take a few seconds.",

                font=("Segoe UI", 8, "italic"),

                bg="#1a1c23",

                fg="#5c6370",

            ).pack(pady=(12, 0))



            splash_root.attributes("-topmost", True)

            splash_root.mainloop()

        except Exception:

            pass



    _splash_thread = threading.Thread(target=show_splash_instantly, daemon=True)

    _splash_thread.start()





if _should_show_splash():

    start_splash_screen()





def create_tray_image():

    # Generate a beautiful 64x64 icon matching Odysseus brand red accent (#e06c75)

    from PIL import Image, ImageDraw

    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))

    dc = ImageDraw.Draw(image)

    accent_red = (224, 108, 117, 255)

    light_red = (224, 108, 117, 150)



    # Draw premium sailing boat

    dc.polygon([(32, 10), (32, 45), (12, 45)], fill=accent_red)

    dc.polygon([(32, 18), (32, 45), (48, 45)], fill=light_red)

    dc.polygon([(8, 48), (56, 48), (44, 56), (20, 56)], fill=accent_red)

    return image





def _stop_tray_icon() -> None:

    global _tray_icon

    icon = _tray_icon

    if icon is None:

        return

    try:

        icon.stop()

    except Exception:

        pass

    _tray_icon = None





def _shutdown_desktop_app() -> None:

    from src.desktop_shutdown import shutdown_odysseus_desktop



    shutdown_odysseus_desktop(_server_controller, stop_tray=_stop_tray_icon)





def on_show_ui(icon, item, url, window=None):

    global _last_show_ui_at

    now = time.monotonic()

    if now - _last_show_ui_at < _SHOW_UI_DEBOUNCE_SEC:

        return

    _last_show_ui_at = now

    if window is not None:

        try:

            window.show()

            window.restore()

            return

        except Exception:

            logger.exception("Не удалось показать окно Odysseus")

            return

    open_external_browser(url)





def on_exit(icon, item):

    _shutdown_desktop_app()





def setup_system_tray(url, window=None):

    global _tray_icon

    if _tray_icon is not None:

        return

    try:

        import pystray

        icon_img = create_tray_image()

        menu = (

            pystray.MenuItem(

                'Open Odysseus',

                lambda icon, item: on_show_ui(icon, item, url, window),

                default=True,

            ),

            pystray.MenuItem('Exit', on_exit),

        )

        tray_icon = pystray.Icon(

            "Odysseus",

            icon_img,

            "Odysseus",

            menu,

        )

        _tray_icon = tray_icon

        tray_icon.run()

    except Exception:

        pass





def close_splash() -> None:

    try:

        global splash_root

        if splash_root:

            splash_root.after(0, splash_root.destroy)

    except Exception:

        pass





def open_browser(url):

    """Legacy browser fallback used when desktop shell is unavailable."""

    time.sleep(3.5)

    close_splash()

    open_external_browser(url)





if __name__ == "__main__":

    if getattr(sys, "frozen", False):

        from src.frozen_runtime import configure_frozen_runtime

        configure_frozen_runtime()

    import uvicorn

    from app import app

    from src.desktop_shutdown import UvicornServerController



    bind_host = os.getenv("APP_BIND", "127.0.0.1")

    bind_port = int(os.getenv("APP_PORT", "7000"))

    url = f"http://{bind_host}:{bind_port}"



    _server_controller = UvicornServerController(app, host=bind_host, port=bind_port)



    def start_server() -> None:

        _server_controller.start()



    if should_use_desktop_shell():

        try:

            run_desktop_app(

                url=url,

                start_server=start_server,

                close_splash=close_splash,

                setup_system_tray=setup_system_tray,

                shutdown_app=_shutdown_desktop_app,

            )

        except RuntimeError:

            if getattr(sys, "frozen", False):

                threading.Thread(target=setup_system_tray, args=(url,), daemon=True).start()

                threading.Thread(target=open_browser, args=(url,), daemon=True).start()

                start_server()

            else:

                raise

    elif getattr(sys, 'frozen', False):

        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

        threading.Thread(target=setup_system_tray, args=(url,), daemon=True).start()

        start_server()

    else:

        uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")


