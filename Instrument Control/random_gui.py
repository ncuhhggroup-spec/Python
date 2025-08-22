import tkinter as tk
from epics import PV


class RandomViewer:
    """Simple Tkinter GUI to display the random PV."""

    def __init__(self, root):
        self.label = tk.Label(root, text='0.000', font=('Arial', 32))
        self.label.pack(padx=30, pady=30)

        # Subscribe to PV updates
        self.pv = PV('Station_Laser:TestDevice:RandomValue',
                     callback=self.on_update)

    def on_update(self, pvname=None, value=None, **kws):
        if value is not None:
            self.label.config(text=f'{value:.3f}')


if __name__ == '__main__':
    root = tk.Tk()
    root.title("Random PV Monitor")
    viewer = RandomViewer(root)
    root.mainloop()
