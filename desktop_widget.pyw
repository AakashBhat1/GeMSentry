"""
GeMSentry Desktop Widget
=========================
A floating, draggable desktop icon that launches the GeMSentry dashboard.

HOW TO RUN:
    Double-click this file, or run:  pythonw desktop_widget.pyw

HOW TO CLOSE:
    • Right-click the icon → click "Exit Widget"
    • Or press Ctrl+Q while the widget is focused
    • Or use Task Manager → End "pythonw" process

FEATURES:
    • Always-on-top floating icon on your desktop
    • Left-click: Launches GeMSentry server & opens dashboard
    • Right-click: Context menu (Open Dashboard / Stop Server / Exit)
    • Draggable: Hold left-click and drag to reposition
    • Hover animation: Glow effect on mouse hover
"""

import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk, ImageFilter, ImageEnhance
import subprocess
import webbrowser
import os
import sys
import signal
import socket
import time
import threading

# ─── Config ──────────────────────────────────────────────────────────────
ICON_SIZE = 72
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(APP_DIR, "desktop_icon.png")
DASHBOARD_URL = "http://127.0.0.1:5000"
PYTHON_EXE = sys.executable  # Use the same Python that launched this widget


class GeMSentryWidget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("GeMSentry")
        self.root.overrideredirect(True)          # Remove title bar
        self.root.attributes("-topmost", True)    # Always on top
        self.root.attributes("-alpha", 0.92)      # Slight transparency
        self.root.configure(bg="black")

        # Try to make window background transparent on Windows
        try:
            self.root.attributes("-transparentcolor", "black")
        except tk.TclError:
            pass

        self.server_process = None
        self._drag_data = {"x": 0, "y": 0}

        # ─── Load & prepare icon ─────────────────────────────────────
        self._load_icon()

        # ─── Create canvas ───────────────────────────────────────────
        self.canvas = tk.Canvas(
            self.root,
            width=ICON_SIZE + 20,
            height=ICON_SIZE + 30,
            bg="black",
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack()

        # Draw the icon
        cx = (ICON_SIZE + 20) // 2
        cy = (ICON_SIZE + 10) // 2
        self.icon_item = self.canvas.create_image(cx, cy, image=self.icon_tk)

        # Status indicator dot
        self.status_dot = self.canvas.create_oval(
            ICON_SIZE - 2, 2, ICON_SIZE + 10, 14,
            fill="#555555", outline="#333333", width=1
        )

        # Label
        self.label = self.canvas.create_text(
            cx, ICON_SIZE + 18,
            text="GeMSentry",
            fill="#AAAAAA",
            font=("Segoe UI", 8, "bold"),
        )

        # ─── Position at bottom-right of screen ─────────────────────
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - ICON_SIZE - 60
        y = screen_h - ICON_SIZE - 100
        self.root.geometry(f"+{x}+{y}")

        # ─── Bindings ───────────────────────────────────────────────
        self.canvas.bind("<Button-1>", self._on_click_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_click_end)
        self.canvas.bind("<Button-3>", self._show_context_menu)
        self.canvas.bind("<Enter>", self._on_hover_enter)
        self.canvas.bind("<Leave>", self._on_hover_leave)
        self.root.bind("<Control-q>", lambda e: self._exit_widget())

        # ─── Context menu ────────────────────────────────────────────
        self.context_menu = tk.Menu(
            self.root, tearoff=0,
            bg="#1e1e2e", fg="#cdd6f4",
            activebackground="#45475a", activeforeground="#cdd6f4",
            font=("Segoe UI", 9),
            relief="flat",
            borderwidth=1,
        )
        self.context_menu.add_command(
            label="🚀  Launch GeMSentry", command=self._launch_app
        )
        self.context_menu.add_command(
            label="🌐  Open Dashboard", command=self._open_dashboard
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="🛑  Stop Server", command=self._stop_server
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="❌  Exit Widget", command=self._exit_widget
        )

        # ─── Tooltip ─────────────────────────────────────────────────
        self.tooltip = None

        # ─── Check if server is already running ──────────────────────
        self._update_status()

        # ─── Periodic status check ───────────────────────────────────
        self._check_status_loop()

    # ═════════════════════════════════════════════════════════════════════
    # Icon Loading
    # ═════════════════════════════════════════════════════════════════════

    def _load_icon(self):
        """Load the icon image and create normal + hover versions."""
        try:
            img = Image.open(ICON_PATH).convert("RGBA")
            img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)

            # Normal icon
            self.icon_img = img
            self.icon_tk = ImageTk.PhotoImage(img)

            # Hover icon (brighter + slight glow)
            enhancer = ImageEnhance.Brightness(img)
            bright = enhancer.enhance(1.3)
            glow = bright.filter(ImageFilter.SMOOTH_MORE)
            self.icon_hover_tk = ImageTk.PhotoImage(glow)

        except FileNotFoundError:
            # Fallback: create a simple colored square icon
            self._create_fallback_icon()

    def _create_fallback_icon(self):
        """Create a simple fallback icon if image not found."""
        img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (66, 133, 244, 220))
        self.icon_img = img
        self.icon_tk = ImageTk.PhotoImage(img)

        bright = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (100, 160, 255, 255))
        self.icon_hover_tk = ImageTk.PhotoImage(bright)

    # ═════════════════════════════════════════════════════════════════════
    # Drag & Click
    # ═════════════════════════════════════════════════════════════════════

    def _on_click_start(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y
        self._drag_data["dragged"] = False

    def _on_drag(self, event):
        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")
        self._drag_data["dragged"] = True

    def _on_click_end(self, event):
        """If it wasn't a drag, treat it as a click → launch app."""
        if not self._drag_data.get("dragged", False):
            self._launch_app()

    # ═════════════════════════════════════════════════════════════════════
    # Hover Effects
    # ═════════════════════════════════════════════════════════════════════

    def _on_hover_enter(self, event):
        self.canvas.itemconfig(self.icon_item, image=self.icon_hover_tk)
        self.canvas.itemconfig(self.label, fill="#FFFFFF")
        self.root.attributes("-alpha", 1.0)

    def _on_hover_leave(self, event):
        self.canvas.itemconfig(self.icon_item, image=self.icon_tk)
        self.canvas.itemconfig(self.label, fill="#AAAAAA")
        self.root.attributes("-alpha", 0.92)

    # ═════════════════════════════════════════════════════════════════════
    # Context Menu
    # ═════════════════════════════════════════════════════════════════════

    def _show_context_menu(self, event):
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    # ═════════════════════════════════════════════════════════════════════
    # Server Management
    # ═════════════════════════════════════════════════════════════════════

    def _is_server_running(self):
        """Check if port 5000 is in use."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(("127.0.0.1", 5000)) == 0
        except Exception:
            return False

    def _update_status(self):
        """Update the status dot color based on server state."""
        if self._is_server_running():
            self.canvas.itemconfig(self.status_dot, fill="#a6e3a1", outline="#40a040")
        else:
            self.canvas.itemconfig(self.status_dot, fill="#555555", outline="#333333")

    def _check_status_loop(self):
        """Periodically check server status."""
        self._update_status()
        self.root.after(5000, self._check_status_loop)  # Check every 5 seconds

    def _launch_app(self):
        """Start the GeMSentry server and open the dashboard."""
        if self._is_server_running():
            self._open_dashboard()
            return

        # Launch server in background
        def start_server():
            try:
                self.server_process = subprocess.Popen(
                    [PYTHON_EXE, "run.py"],
                    cwd=APP_DIR,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                # Wait a bit for server to start, then open browser
                time.sleep(3)
                if self._is_server_running():
                    self.root.after(0, self._open_dashboard)
                    self.root.after(0, self._update_status)
            except Exception as e:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "GeMSentry Error",
                        f"Failed to start server:\n{e}"
                    ),
                )

        thread = threading.Thread(target=start_server, daemon=True)
        thread.start()

        # Flash the status dot to indicate starting
        self.canvas.itemconfig(self.status_dot, fill="#f9e2af", outline="#e0a020")

    def _open_dashboard(self):
        """Open the dashboard in the default browser."""
        webbrowser.open(DASHBOARD_URL)

    def _stop_server(self):
        """Stop the GeMSentry server process."""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except Exception:
                self.server_process.kill()
            self.server_process = None

        self._update_status()

    def _exit_widget(self):
        """Clean up and exit the widget."""
        if self.server_process:
            result = messagebox.askyesnocancel(
                "GeMSentry",
                "The server is still running.\n\n"
                "• Yes = Stop server & exit\n"
                "• No = Exit widget only (server keeps running)\n"
                "• Cancel = Don't exit",
            )
            if result is True:
                self._stop_server()
            elif result is None:
                return  # Cancel

        self.root.destroy()

    # ═════════════════════════════════════════════════════════════════════
    # Run
    # ═════════════════════════════════════════════════════════════════════

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    widget = GeMSentryWidget()
    widget.run()
