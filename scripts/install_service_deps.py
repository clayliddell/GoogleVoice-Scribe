from __future__ import annotations

import os
import sys

from script_common import REPO_ROOT, run


PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu124"
PYTORCH_PACKAGES = (
    "torch==2.6.0+cu124",
    "torchaudio==2.6.0+cu124",
    "torchvision==0.21.0+cu124",
)
TORCH_FAMILY_PACKAGES = ("torch", "torchaudio", "torchvision", "torchcodec")


def main() -> int:
    python = sys.executable
    requirements = REPO_ROOT / "service" / "requirements.txt"
    if not requirements.exists():
        raise SystemExit(f"Missing requirements file: {requirements}")

    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "uninstall", "-y", *TORCH_FAMILY_PACKAGES])
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--index-url",
            PYTORCH_INDEX_URL,
            *PYTORCH_PACKAGES,
        ]
    )
    run([python, "-m", "pip", "install", "-r", requirements])
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            "--prefer-binary",
            "llama-cpp-python",
            "--extra-index-url",
            "https://abetlen.github.io/llama-cpp-python/whl/cu124",
        ]
    )

    check = (
        "import os, sys; "
        "torch_lib = os.path.join(sys.prefix, 'Lib', 'site-packages', 'torch', 'lib'); "
        "os.add_dll_directory(torch_lib) if os.path.isdir(torch_lib) else None; "
        "import torch, torchaudio, torchvision; "
        "from llama_cpp import llama_cpp; "
        "print(torch.__version__); print(torchaudio.__version__); print(torchvision.__version__); "
        "assert torch.__version__.startswith('2.6.0+cu124'), torch.__version__; "
        "assert torchaudio.__version__.startswith('2.6.0+cu124'), torchaudio.__version__; "
        "assert torchvision.__version__.startswith('0.21.0+cu124'), torchvision.__version__; "
        "print('cuda_available=', torch.cuda.is_available()); "
        "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda device'); "
        "print('llama_cpp_gpu_offload=', bool(llama_cpp.llama_supports_gpu_offload()))"
    )
    run([python, "-c", check])
    run([python, "-m", "pip", "check"])
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
