"""
RandomValue IOC with Start/Stop and a simple Tkinter UI.

Requirements:
  - pythonSoftIOC (pip install pythonSoftIOC)
  - tkinter (standard library on most Python installs)

This publishes:
  - Station_Laser:TestDevice:RandomValue  (ai, readback, PREC=3)
  - Station_Laser:TestDevice:Enable       (bo, 0/1 start-stop)

UI:
  - Start/Stop buttons toggle 'Enable'
  - Displays PV name, current value, and enabled state

Change in this revision:
  - FIX for Windows AssertionError: we DO NOT call loop.run_forever() ourselves.
    AsyncioDispatcher runs its own loop thread, so we create the IOC on the main
    thread and just run Tk's mainloop. This avoids the background-thread
    run_forever() assertion on Windows.
  - Pressing Start updates the value immediately and then every 1 second.
  - Clean shutdown on window close.
"""

import random
import tkinter as tk
from tkinter import ttk
import asyncio

from softioc import softioc, builder, asyncio_dispatcher


# ---------------------- IOC Layer (OOP) ----------------------
class RandomValueIOC:
    """OOP wrapper around pythonSoftIOC that publishes a random value.

    Public API:
      - set_on_value_callback(cb: Callable[[float, bool], None])
      - start()  -> enable periodic updates
      - stop()   -> disable periodic updates
    """

    def __init__(self, prefix: str = "Station_Laser:TestDevice", period_s: float = 1.0):
        self.prefix = prefix
        self.period = float(period_s)
        self._enabled = False
        self._task: asyncio.Task | None = None
        self._on_value_callback = None  # UI hook

        # Create dispatcher (runs an asyncio loop in its OWN background thread)
        self.dispatcher = asyncio_dispatcher.AsyncioDispatcher()
        self.loop: asyncio.AbstractEventLoop = self.dispatcher.loop

        # Build EPICS database
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

        # Enable control (bo) with fallback to longIn/longOut if needed
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
            # Fallback: integer 0/1 via longin/longout
            self.enable_li = builder.longIn("Enable_RBV", initial_value=0)
            self.enable_lo = builder.longOut(
                "Enable",
                initial_value=0,
                on_update=lambda v: self._on_enable_update(bool(int(v))),
            )
            self._use_bool = False

        builder.LoadDatabase()

        # Initialize IOC — the dispatcher's loop thread is already running
        softioc.iocInit(self.dispatcher)

    # ---------- Public API ----------
    def set_on_value_callback(self, cb):
        """UI can register a callback: cb(value: float, enabled: bool)."""
        self._on_value_callback = cb

    def start(self):
        """Enable updates (thread-safe)."""
        if self._use_bool:
            self.enable_bo.set(True)
        else:
            self.enable_lo.set(1)
            self.enable_li.set(1)
        self._set_enabled(True)

    def stop(self):
        """Disable updates (thread-safe)."""
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
        # Marshal into asyncio loop thread if needed
        def _apply():
            if enable and not self._enabled:
                self._enabled = True
                if not self._task or self._task.done():
                    self._task = asyncio.create_task(self._run_updates())
            elif not enable and self._enabled:
                self._enabled = False
                if self._task and not self._task.done():
                    self._task.cancel()

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            _apply()
        else:
            self.loop.call_soon_threadsafe(_apply)

    async def _run_updates(self):
        """Immediately push a random value, then update every `period`."""
        try:
            while self._enabled:
                val = random.random()
                self.random_ai.set(val)
                if self._on_value_callback:
                    self._on_value_callback(val, self._enabled)
                await asyncio.sleep(self.period)
        except asyncio.CancelledError:
            pass


# ---------------------- UI Layer (Tkinter) ----------------------
class RandomValueUI(tk.Tk):
    def __init__(self, ioc: RandomValueIOC):
        super().__init__()
        self.title("EPICS RandomValue IOC")
        self.geometry("440x190")
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
        ttk.Entry(frm, textvariable=self.pv_name_var, width=42, state="readonly").grid(row=0, column=1, sticky="w")

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

        # Safe close
        self.protocol("WM_DELETE_WINDOW", self.on_close)

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

    def on_close(self):
        # Gracefully stop on window close
        self.on_stop()
        self.destroy()


# ---------------------- Wiring & Run ----------------------

def run_ioc_and_ui():
    """Create IOC (whose loop runs in background) and start Tk UI on main thread."""
    ioc = RandomValueIOC(prefix="Station_Laser:TestDevice", period_s=1.0)
    ui = RandomValueUI(ioc)
    try:
        ui.mainloop()
    finally:
        # Ensure IOC stops tasks
        ioc.stop()


if __name__ == "__main__":
    run_ioc_and_ui()
