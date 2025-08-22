"""
RandomValue IOC with Start/Stop and a simple Tkinter UI.

Requires:
  - pythonSoftIOC (pip install pythonSoftIOC)
  - tkinter (standard library on most Python installs)

This publishes:
  - Station_Laser:TestDevice:RandomValue  (ai, readback, PREC=3)
  - Station_Laser:TestDevice:Enable       (bo, 0/1 start-stop)

UI:
  - Start/Stop buttons toggle 'Enable'
  - Displays PV name, current value, and enabled state
"""

import threading
import random
import time
import tkinter as tk
from tkinter import ttk

from softioc import softioc, builder, asyncio_dispatcher
import asyncio


# ---------------------- IOC Layer (OOP) ----------------------
class RandomValueIOC:
    def __init__(self, prefix: str = "Station_Laser:TestDevice", period_s: float = 1.0):
        self.prefix = prefix
        self.period = period_s
        self._enabled = False
        self._task = None
        self._on_value_callback = None  # UI hook

        # Create dispatcher (asyncio loop in this process)
        self.dispatcher = asyncio_dispatcher.AsyncioDispatcher()
        self.loop: asyncio.AbstractEventLoop = self.dispatcher.loop

        # Build database
        builder.SetDeviceName(self.prefix)

        # Readback value (ai)
        self.random_ai = builder.aIn(
            "RandomValue",
            EGU="arb",
            PREC=3,
            LOPR=0.0,
            HOPR=1.0,
            initial_value=0.0,
        )

        # Enable control (bo). If your pythonSoftIOC lacks boolOut,
        # comment the boolOut lines and use the longOut/longIn alternative below.
        try:
            self.enable_bo = builder.boolOut(
                "Enable",
                ZNAM="Disabled",
                ONAM="Enabled",
                initial_value=False,
                on_update=self._on_enable_update,
            )
            self._use_bool = True
        except Exception:
            # Fallback: use integer 0/1 via longin/longout
            self.enable_li = builder.longIn("Enable_RBV", initial_value=0)
            self.enable_lo = builder.longOut(
                "Enable",
                initial_value=0,
                on_update=lambda v: self._on_enable_update(bool(int(v))),
            )
            self._use_bool = False

        builder.LoadDatabase()

        # Initialize IOC — NOTE: this must run in the same thread that owns the loop
        softioc.iocInit(self.dispatcher)

    # ---------- Public API ----------
    def set_on_value_callback(self, cb):
        """UI can register a callback: cb(value: float, enabled: bool)."""
        self._on_value_callback = cb

    def start(self):
        """Enable updates (can be called from any thread)."""
        if self._use_bool:
            self.enable_bo.set(True)
        else:
            self.enable_lo.set(1)
            self.enable_li.set(1)
        self._set_enabled(True)

    def stop(self):
        """Disable updates (can be called from any thread)."""
        if self._use_bool:
            self.enable_bo.set(False)
        else:
            self.enable_lo.set(0)
            self.enable_li.set(0)
        self._set_enabled(False)

    # ---------- Internals ----------
    def _on_enable_update(self, value: bool):
        # Callback from EPICS client writes (bo/longOut)
        self._set_enabled(bool(value))

    def _set_enabled(self, enable: bool):
        # This may be called from UI thread. Marshal into asyncio loop.
        def _apply():
            if enable and not self._enabled:
                self._enabled = True
                if not self._task or self._task.done():
                    self._task = asyncio.create_task(self._run_updates())
            elif not enable and self._enabled:
                self._enabled = False
                if self._task and not self._task.done():
                    self._task.cancel()

        # Ensure we modify state in the asyncio loop thread
        if asyncio.get_event_loop() is self.loop:
            _apply()
        else:
            self.loop.call_soon_threadsafe(_apply)

    async def _run_updates(self):
        try:
            while self._enabled:
                val = random.random()
                # Update PV
                self.random_ai.set(val)
                # Notify UI (if registered)
                if self._on_value_callback:
                    # Call UI callback in a safe way (we only schedule; UI will marshal to its thread)
                    self._on_value_callback(val, self._enabled)
                await asyncio.sleep(self.period)
        except asyncio.CancelledError:
            pass


# ---------------------- UI Layer (Tkinter) ----------------------
class RandomValueUI(tk.Tk):
    def __init__(self, ioc: RandomValueIOC):
        super().__init__()
        self.title("EPICS RandomValue IOC")
        self.geometry("420x180")
        self.resizable(False, False)

        self.ioc = ioc

        # Tk variables
        self.pv_name_var = tk.StringVar(value=f"{ioc.prefix}:RandomValue")
        self.value_var = tk.StringVar(value="0.000")
        self.state_var = tk.StringVar(value="Disabled")

        # Layout
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        ttk.Label(frm, text="PV Name:").grid(row=0, column=0, sticky="e")
        ttk.Entry(frm, textvariable=self.pv_name_var, width=40, state="readonly").grid(row=0, column=1, sticky="w")

        ttk.Label(frm, text="Current Value:").grid(row=1, column=0, sticky="e")
        self.value_label = ttk.Label(frm, textvariable=self.value_var, font=("Segoe UI", 12, "bold"))
        self.value_label.grid(row=1, column=1, sticky="w")

        ttk.Label(frm, text="State:").grid(row=2, column=0, sticky="e")
        self.state_label = ttk.Label(frm, textvariable=self.state_var)
        self.state_label.grid(row=2, column=1, sticky="w")

        # Buttons
        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        start_btn = ttk.Button(btn_frame, text="Start", command=self.on_start)
        stop_btn = ttk.Button(btn_frame, text="Stop", command=self.on_stop)
        start_btn.grid(row=0, column=0, padx=8)
        stop_btn.grid(row=0, column=1, padx=8)

        # Hook IOC callbacks
        self.ioc.set_on_value_callback(self.on_new_value_from_ioc)

        # Periodic UI poll to refresh enabled state (in case external clients toggle it)
        self.after(250, self.poll_enabled_state)

    # ----- Button handlers -----
    def on_start(self):
        self.ioc.start()
        self.state_var.set("Enabled")

    def on_stop(self):
        self.ioc.stop()
        self.state_var.set("Disabled")

    # ----- IOC -> UI callback -----
    def on_new_value_from_ioc(self, value: float, enabled: bool):
        # Must marshal to Tk thread
        self.after(0, lambda: self._apply_value(value, enabled))

    def _apply_value(self, value: float, enabled: bool):
        self.value_var.set(f"{value:0.3f}")
        self.state_var.set("Enabled" if enabled else "Disabled")

    # ----- Periodic poll (optional) -----
    def poll_enabled_state(self):
        # If you added a readback for enable, you could reflect it here.
        # For now, we rely on callbacks + button handlers.
        self.after(250, self.poll_enabled_state)


# ---------------------- Wiring & Run ----------------------
def run_ioc_and_ui():
    # Create IOC in a background thread (so Tk mainloop can own the main thread)
    ioc_ready = threading.Event()
    ioc_holder = {}

    def ioc_thread():
        ioc = RandomValueIOC(prefix="Station_Laser:TestDevice", period_s=1.0)
        ioc_holder["ioc"] = ioc
        ioc_ready.set()
        # Keep the asyncio loop alive forever in this thread
        # The IOC remains active while the process runs.
        try:
            ioc.loop.run_forever()
        except KeyboardInterrupt:
            pass

    t = threading.Thread(target=ioc_thread, daemon=True)
    t.start()

    # Wait for IOC to be initialized
    ioc_ready.wait()
    ioc = ioc_holder["ioc"]

    # Launch UI on main thread
    ui = RandomValueUI(ioc)
    ui.mainloop()

    # When UI closes, stop IOC loop gracefully
    ioc.loop.call_soon_threadsafe(ioc.loop.stop)

if __name__ == "__main__":
    run_ioc_and_ui()
