# cv-models/Dockerfile
FROM runpod/pytorch:1.0.2-cu1281-torch271-ubuntu2204

WORKDIR /app

# Install Python deps — exclude torch/torchvision (already in base image)
RUN pip install --no-cache-dir \
    "fastapi==0.115.0" \
    "uvicorn==0.30.0" \
    "python-multipart==0.0.12" \
    "numpy==1.26.4" \
    "transformers==4.44.2" \
    "scikit-learn==1.5.2" \
    "joblib==1.4.2" \
    "Pillow==10.4.0" \
    runpod

# Pre-download DINOv2 weights so cold start doesn't fetch from HuggingFace
RUN python -c "\
from transformers import AutoImageProcessor, AutoModel; \
AutoImageProcessor.from_pretrained('facebook/dinov2-base'); \
AutoModel.from_pretrained('facebook/dinov2-base'); \
print('DINOv2 cached.')"

# Copy inference code and trained artifacts
COPY app/ app/
COPY scripts/ scripts/
COPY artifacts/ artifacts/
COPY handler.py .

CMD ["python", "-u", "handler.py"]
