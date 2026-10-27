"""Matplotlib backend for scripts run from the vonNeuro VS Code extension: plt.show() writes each
open figure as a PNG (in MESSENGER_FIGURE_DIR, else the output folder) and the extension opens it."""
import os
from pathlib import Path

from matplotlib._pylab_helpers import Gcf
from matplotlib.backend_bases import FigureManagerBase
from matplotlib.backends.backend_agg import FigureCanvasAgg

from ._client import _progress

FigureCanvas = FigureCanvasAgg
FigureManager = FigureManagerBase
_shown = 0


def show(*args, **kwargs):
    global _shown
    folder = Path(os.environ.get("MESSENGER_FIGURE_DIR") or os.environ.get("MESSENGER_OUTPUT_DIR") or ".")
    folder.mkdir(parents=True, exist_ok=True)
    for manager in Gcf.get_all_fig_managers():
        _shown += 1
        path = folder / f"figure_{_shown}.png"
        manager.canvas.figure.savefig(path, dpi=120, bbox_inches="tight")
        _progress({"phase": "figure", "path": str(path.resolve())})
    Gcf.destroy_all()
