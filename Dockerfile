FROM nvidia/cuda:13.1.1-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CUDA_STUB_DIR=/usr/local/cuda-13.1/targets/x86_64-linux/lib/stubs

ENV LIBRARY_PATH="${CUDA_STUB_DIR}:${LIBRARY_PATH}"
ENV LDFLAGS="-L${CUDA_STUB_DIR} -Wl,-rpath-link,${CUDA_STUB_DIR}"

# ============================================================
# System dependencies
# ============================================================

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    git \
    build-essential \
    cmake \
    ninja-build \
    pkg-config \
    libssl-dev \
    wget \
    curl \
    cuda-driver-dev-13-1 \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# Build llama.cpp with CUDA support
# ============================================================

WORKDIR /opt

RUN git clone https://github.com/ggml-org/llama.cpp.git

WORKDIR /opt/llama.cpp

RUN cmake -B build \
    -DGGML_CUDA=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -G Ninja \
    -DCMAKE_CUDA_ARCHITECTURES=120 \
    -DCMAKE_EXE_LINKER_FLAGS="-L${CUDA_STUB_DIR} -Wl,-rpath-link,${CUDA_STUB_DIR}" \
    -DCMAKE_SHARED_LINKER_FLAGS="-L${CUDA_STUB_DIR} -Wl,-rpath-link,${CUDA_STUB_DIR}" \
    && cmake --build build --config Release -j$(nproc)


# ============================================================
# Build stable-diffusion.cpp with CUDA support
# ============================================================

WORKDIR /opt

RUN git clone https://github.com/leejet/stable-diffusion.cpp.git

WORKDIR /opt/stable-diffusion.cpp

RUN cmake -B build \
    -DGGML_CUDA=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -G Ninja \
    -DCMAKE_CUDA_ARCHITECTURES=120 \
    -DCMAKE_EXE_LINKER_FLAGS="-L${CUDA_STUB_DIR} -Wl,-rpath-link,${CUDA_STUB_DIR}" \
    -DCMAKE_SHARED_LINKER_FLAGS="-L${CUDA_STUB_DIR} -Wl,-rpath-link,${CUDA_STUB_DIR}" \
    && cmake --build build --config Release -j$(nproc)


# ============================================================
# Make llama.cpp and sd-cli executables available system-wide
# ============================================================

ENV PATH="/opt/llama.cpp/build/bin:/opt/stable-diffusion.cpp/build/bin:${PATH}"


# ============================================================
# AI Gateway
# ============================================================

WORKDIR /app

RUN mkdir -p /app/ImageGen

COPY ai-gateway/requirements.txt .

RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY ai-gateway/ .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1


# ============================================================
# Network
# ============================================================

EXPOSE 8000


# ============================================================
# Start FastAPI gateway
# ============================================================

CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]