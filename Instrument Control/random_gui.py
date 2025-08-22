"""
Caproto-based EPICS CA subscriber with a Tkinter GUI (OOP style).

Why Caproto? Pure-Python CA client → no external caRepeater.exe needed on Windows.

Update (polling):
- Now the GUI **keeps updating continuously** (default every 1 s) until you press Stop.
- Uses a Tk `after()` polling loop calling a safe `read_now()` on the channel.
- Subscriptions are still set for quick first update, but polling guarantees steady refresh.

Install
  pip install caproto

Run
  python CaprotoSubscriber_Tk_GUI.py
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Callable
import tkinter as tk
from tkinter import ttk, messagebox

try:
    from caproto.threading.client import Context
except Exception as e:
    raise SystemExit("Caproto is required. Install with: pip install caproto\n" + str(e))


# ---------------------- Data Model ----------------------
@dataclass
class PVUpdate:
    value: float
    ts: float


# ---------------------- EPICS Subscriber (OOP) ----------------------
class CaprotoSubscriber:
    """Small wrapper around caproto.threading.client.Context for one PV."""

    def __init__(self, pvname: str, on_update: Optional[Callable[[PVUpdate], None]] = None, on_conn: Optional[Callable[[bool], None]] = None):
        self.pvname = pvname
        self.on_update = on_update
        self.on_conn = on_conn

        self._ctx: Optional[Context] = None
        self._chan = None
        self._sub = None

    def start(self):
        # Create context and connect
        self._ctx = Context()
        self._chan, = self._ctx.get_pvs(self.pvname)
        self._chan.wait_for_connection(timeout=5.0)
        if self.on_conn:
            self.on_conn(True)

        # Subscribe for time-stamped updates (optional, for immediate updates)
        self._sub = self._chan.subscribe(data_type='time')
        self._sub.add_callback(self._callback)

        # Emit initial value if available
        try:
            upd = self.read_now()
            if upd and self.on_update:
                self.on_update(upd)
        except Exception:
            pass

    def stop(self):
        try:
            if self._sub is not None:
                self._sub.remove_callback(self._callback)
                self._sub = None
        finally:
            if self._ctx is not None:
                try:
                    self._ctx.disconnect()
                finally:
                    self._ctx = None
            if self.on_conn:
                self.on_conn(False)

    def read_now(self) -> Optional[PVUpdate]:
        """Synchronous read of current value with a best-effort timestamp."""
        if self._chan is None:
            return None
        try:
            resp = self._chan.read(data_type='time')
            val = float(resp.data[0]) if getattr(resp, 'data', None) else float('nan')
            ts = getattr(getattr(resp, 'metadata', None), 'timestamp', None)
            ts = float(ts) if ts is not None else time.time()
            return PVUpdate(val, ts)
        except Exception:
            return None

    # ----- Internal callback from caproto worker thread -----
    def _callback(self, value=None, timestamp=None, **kw):
        if not self.on_update:
            return
        try:
            if hasattr(value, '__len__'):
                v = float(value[0])
            else:
                v = float(value)
        except Exception:
            return
        ts = float(timestamp) if timestamp is not None else time.time()
        self.on_update(PVUpdate(v, ts))


# ---------------------- Tkinter UI (OOP) ----------------------
class SubscriberTkUI(tk.Tk):
    def __init__(self, default_pv: str = "Station_Laser:TestDevice:RandomValue"):
        super().__init__()
        self.title("EPICS PV Monitor (Caproto)")
        self.geometry("600x240")
        self.resizable(False, False)

        # State variables
        self.pvname_var = tk.StringVar(value=default_pv)
        self.value_var = tk.StringVar(value="—")
        self.time_var = tk.StringVar(value="—")
        self.conn_var = tk.StringVar(value="Disconnected")
        self.period_var = tk.DoubleVar(value=1.0)

        # Layout
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        ttk.Label(frm, text="PV Name:").grid(row=0, column=0, sticky="e")
        self.pv_entry = ttk.Entry(frm, textvariable=self.pvname_var, width=48)
        self.pv_entry.grid(row=0, column=1, sticky="w")

        ttk.Label(frm, text="Current Value:").grid(row=1, column=0, sticky="e")
        ttk.Label(frm, textvariable=self.value_var, font=("Segoe UI", 12, "bold")).grid(row=1, column=1, sticky="w")

        ttk.Label(frm, text="Last Update:").grid(row=2, column=0, sticky="e")
        ttk.Label(frm, textvariable=self.time_var).grid(row=2, column=1, sticky="w")

        ttk.Label(frm, text="Connection:").grid(row=3, column=0, sticky="e")
        ttk.Label(frm, textvariable=self.conn_var).grid(row=3, column=1, sticky="w")

        ttk.Label(frm, text="Period (s):").grid(row=4, column=0, sticky="e")
        ttk.Entry(frm, textvariable=self.period_var, width=10).grid(row=4, column=1, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text="Start", command=self.on_start).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="Stop", command=self.on_stop).grid(row=0, column=1, padx=6)

        self.subscriber: Optional[CaprotoSubscriber] = None
        self._polling = False
        self._poll_id: Optional[str] = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- UI actions -----
    def on_start(self):
        pv = self.pvname_var.get().strip()
        if not pv:
            messagebox.showwarning("Missing PV", "Please enter a PV name.")
            return
        self.on_stop()  # stop any existing subscriber/polling
        self.subscriber = CaprotoSubscriber(pv, on_update=self._on_update_from_pv, on_conn=self._on_conn_state)
        try:
            self.conn_var.set("Connecting…")
            self.subscriber.start()
        except Exception as e:
            self.conn_var.set("Disconnected")
            messagebox.showerror("Connection error", str(e))
            return
        # Start polling loop for continuous updates
        self._polling = True
        self._schedule_poll()

    def on_stop(self):
        # stop polling first
        self._polling = False
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        # then stop subscriber
        if self.subscriber is not None:
            try:
                self.subscriber.stop()
            finally:
                self.subscriber = None
        self.conn_var.set("Disconnected")

    def on_close(self):
        self.on_stop()
        self.destroy()

    # ----- Polling loop -----
    def _schedule_poll(self):
        if not self._polling:
            return
        period_ms = max(50, int(self.period_var.get() * 1000))
        self._poll_id = self.after(period_ms, self._poll_once)

    def _poll_once(self):
        if not (self._polling and self.subscriber):
            return
        upd = self.subscriber.read_now()
        if upd is not None:
            self._on_update_from_pv(upd)
        self._schedule_poll()

    # ----- Callbacks from subscriber (caproto thread → Tk thread) -----
    def _on_update_from_pv(self, upd: PVUpdate):
        self.after(0, lambda: self._apply_update(upd))

    def _apply_update(self, upd: PVUpdate):
        self.value_var.set(f"{upd.value:0.3f}")
        self.time_var.set(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(upd.ts)))

    def _on_conn_state(self, connected: bool):
        self.after(0, lambda: self.conn_var.set("Connected" if connected else "Disconnected"))


# ---------------------- Entrypoint ----------------------
if __name__ == "__main__":
    ui = SubscriberTkUI()
    ui.mainloop()
