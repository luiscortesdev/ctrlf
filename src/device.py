"""
Where the models run: the GPU if there is one, else the CPU, on the Mac and on Windows.

Order: NVIDIA (CUDA), Intel (XPU: Core Ultra / Arc graphics, needs the XPU build of torch),
Apple (MPS), then the CPU. scripts/bench_platform.py prints which one it picked.

On the CPU, torch uses every core by default. The camera, the live view and Whisper need
some too, so a quarter of them (at most 4) are left free. On a Core Ultra 7 356H (16
cores) OWLv2 at 768 took 1.08 s per frame with 16 threads, 1.05 s with 12, 1.39 s with 8
and 1.89 s with 4: going lower than that only slows it down. CTRLF_TORCH_THREADS=<n>
overrides it.
"""

import os

import torch

_picked = None


def cpu_threads():
    """How many threads torch gets on the CPU."""
    wanted = os.environ.get("CTRLF_TORCH_THREADS")
    if wanted:
        return max(1, int(wanted))
    cores = os.cpu_count() or 1
    return max(1, cores - min(4, cores // 4))


def get_torch_device():
    """The fastest device torch can use here (worked out once)."""
    global _picked
    if _picked is None:
        if torch.cuda.is_available():
            _picked = torch.device("cuda:0")
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            _picked = torch.device("xpu")
        elif torch.backends.mps.is_available():
            _picked = torch.device("mps")
        else:
            _picked = torch.device("cpu")
            torch.set_num_threads(cpu_threads())
    return _picked


def half_precision(device):
    """float16 is about twice as fast on GPUs; on the CPU it's slower, so float32 there."""
    return torch.device(device).type != "cpu"
