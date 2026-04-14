"""Sandboxed Python/R code execution for the AI Statistician.

Runs scripts in subprocess with timeout. Returns stdout, stderr,
and any generated image files as base64-encoded PNGs.
"""

import base64
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("rubricgen")

# Maximum execution time in seconds
DEFAULT_TIMEOUT = 60

# Rate limiting state (per-user, in-memory)
_execution_counts: dict[int, list[float]] = {}


def _check_rate_limit(user_id: int, max_per_hour: int = 10) -> bool:
    """Check if user has exceeded the rate limit. Returns True if OK."""
    import time
    now = time.time()
    cutoff = now - 3600  # 1 hour window

    if user_id not in _execution_counts:
        _execution_counts[user_id] = []

    # Prune old entries
    _execution_counts[user_id] = [t for t in _execution_counts[user_id] if t > cutoff]

    if len(_execution_counts[user_id]) >= max_per_hour:
        return False

    _execution_counts[user_id].append(now)
    return True


def run_python_analysis(code: str, user_id: int, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Execute a Python script in a sandboxed subprocess.

    Returns dict with:
      stdout: str - standard output
      stderr: str - standard error
      images: list[str] - base64 PNG images
      success: bool
    """
    if not _check_rate_limit(user_id):
        return {
            "stdout": "",
            "stderr": "Rate limit exceeded: max 10 executions per hour.",
            "images": [],
            "success": False,
        }

    with tempfile.TemporaryDirectory(prefix="rubricgen_py_") as tmpdir:
        script_path = Path(tmpdir) / "analysis.py"
        img_dir = Path(tmpdir) / "images"
        img_dir.mkdir()

        # Wrap the user code to redirect matplotlib output
        wrapper = f"""
import sys
import os
os.environ['MPLBACKEND'] = 'Agg'
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_img_dir = {str(img_dir)!r}
_img_counter = [0]

_orig_show = plt.show
def _save_show(*args, **kwargs):
    _img_counter[0] += 1
    path = os.path.join(_img_dir, f"fig_{{_img_counter[0]:03d}}.png")
    plt.savefig(path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close('all')
plt.show = _save_show

# User code below
{code}

# Save any remaining figures
for num in plt.get_fignums():
    _img_counter[0] += 1
    path = os.path.join(_img_dir, f"fig_{{_img_counter[0]:03d}}.png")
    plt.figure(num).savefig(path, dpi=150, bbox_inches='tight', facecolor='white')
plt.close('all')
"""
        script_path.write_text(wrapper, encoding="utf-8")

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
                env={
                    **os.environ,
                    "MPLBACKEND": "Agg",
                },
            )
            stdout = result.stdout
            stderr = result.stderr
            success = result.returncode == 0
        except subprocess.TimeoutExpired:
            stdout = ""
            stderr = f"Execution timed out after {timeout} seconds."
            success = False
        except Exception as e:
            stdout = ""
            stderr = str(e)
            success = False

        # Collect generated images
        images = []
        for img_path in sorted(img_dir.glob("*.png")):
            try:
                img_data = img_path.read_bytes()
                images.append(base64.b64encode(img_data).decode("ascii"))
            except Exception as e:
                logger.warning("Failed to read image %s: %s", img_path, e)

        return {
            "stdout": stdout[:10000],  # Cap output size
            "stderr": stderr[:5000],
            "images": images,
            "success": success,
        }


def run_r_analysis(code: str, user_id: int, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Execute an R script in a sandboxed subprocess.

    Returns dict with:
      stdout: str - standard output
      stderr: str - standard error
      images: list[str] - base64 PNG images
      success: bool
    """
    if not _check_rate_limit(user_id):
        return {
            "stdout": "",
            "stderr": "Rate limit exceeded: max 10 executions per hour.",
            "images": [],
            "success": False,
        }

    with tempfile.TemporaryDirectory(prefix="rubricgen_r_") as tmpdir:
        script_path = Path(tmpdir) / "analysis.R"
        img_dir = Path(tmpdir) / "images"
        img_dir.mkdir()

        # Wrap R code to save plots
        wrapper = f"""
img_dir <- "{img_dir}"
img_counter <- 0

# Override plot device to save to files
.save_plot <- function() {{
  img_counter <<- img_counter + 1
  path <- file.path(img_dir, sprintf("fig_%03d.png", img_counter))
  dev.copy(png, filename=path, width=800, height=600, res=150)
  dev.off()
}}

# User code
{code}

# Save any open plots
if (dev.cur() > 1) {{
  img_counter <- img_counter + 1
  path <- file.path(img_dir, sprintf("fig_%03d.png", img_counter))
  dev.copy(png, filename=path, width=800, height=600, res=150)
  dev.off()
}}
"""
        script_path.write_text(wrapper, encoding="utf-8")

        try:
            result = subprocess.run(
                ["Rscript", str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
            )
            stdout = result.stdout
            stderr = result.stderr
            success = result.returncode == 0
        except FileNotFoundError:
            return {
                "stdout": "",
                "stderr": "R is not installed or Rscript is not in PATH.",
                "images": [],
                "success": False,
            }
        except subprocess.TimeoutExpired:
            stdout = ""
            stderr = f"Execution timed out after {timeout} seconds."
            success = False
        except Exception as e:
            stdout = ""
            stderr = str(e)
            success = False

        # Collect generated images
        images = []
        for img_path in sorted(img_dir.glob("*.png")):
            try:
                img_data = img_path.read_bytes()
                images.append(base64.b64encode(img_data).decode("ascii"))
            except Exception as e:
                logger.warning("Failed to read R image %s: %s", img_path, e)

        return {
            "stdout": stdout[:10000],
            "stderr": stderr[:5000],
            "images": images,
            "success": success,
        }
