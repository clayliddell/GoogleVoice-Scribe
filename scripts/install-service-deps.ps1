$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Missing .venv. Create it first with: py -3.12 -m venv .venv"
}

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $repoRoot "service\requirements.txt")

# The default Windows PyPI torch wheel is CPU-only. Install CUDA wheels explicitly.
& $python -m pip install --force-reinstall torch==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124
& $python -m pip install torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124
& $python -m pip install torchvision==0.21.0+cu124 --index-url https://download.pytorch.org/whl/cu124

# Install the CUDA-enabled llama.cpp bindings used by GGUF title generation.
& $python -m pip install --upgrade --force-reinstall --no-cache-dir --prefer-binary llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124

& $python -c "import os, sys; torch_lib = os.path.join(sys.prefix, 'Lib', 'site-packages', 'torch', 'lib'); os.add_dll_directory(torch_lib) if os.path.isdir(torch_lib) else None; import torch, torchaudio, torchvision; from llama_cpp import llama_cpp; print(torch.__version__); print(torchaudio.__version__); print(torchvision.__version__); print('cuda_available=', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda device'); print('llama_cpp_gpu_offload=', bool(llama_cpp.llama_supports_gpu_offload()))"
