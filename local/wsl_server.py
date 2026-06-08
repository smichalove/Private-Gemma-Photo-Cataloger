"""WSL2 Backend Server for Gemma 4 VLM - VLM Inference Server.

SYSTEM ROLE & ARCHITECTURE:
This module runs inside a WSL2 GPU-enabled Docker container as a FastAPI REST microservice. 
It loads, quantizes, and hosts Google's Gemma 4 12B IT Vision-Language Model (VLM), serving 
on-demand batch inference requests triggered from the Windows host environment.

                   +--------------------------------------------+
                   |                 FastAPI (app)              |
                   |  +--------------------------------------+  |
                   |  |          POST /describe              |  |
                   |  +------------------+-------------------+  |
                   +---------------------|----------------------+
                                         | (decodes Base64 to PIL)
                                         v
                   +---------------------v----------------------+
                   |         HuggingFace VLM Processor          |
                   |  (Applies Chat Template & Prefix Prefill)  |
                   +---------------------+----------------------+
                                         |
                                         v
                   +---------------------v----------------------+
                   |         Gemma 4 12B IT (quantized 4-bit)   |
                   |   (Patched forward pass via CUDA:0 GPU)    |
                   +--------------------------------------------+

EXECUTION WORKFLOW:
1. Startup Event: Automatically triggered when Uvicorn launches.
   - Applies `patch_gemma4_unified()` to correct a known BitsAndBytes 4-bit LayerNorm precision 
     casting bug in the transformers library.
   - Checks if a pre-quantized 4-bit model checkpoint exists on disk (/workspace/models/). 
     If so, loads it directly. If not, loads the base model from cache, performs on-the-fly 
     4-bit quantization, and saves the resulting quantized checkpoint to speed up future startups.
   - Initialized processor and model onto GPU CUDA:0 using bfloat16.
2. Inference API (/describe): Receives lists of Base64-encoded image payloads and prompt text.
   - Converts the base64 arrays back into Pillow RGB images.
   - Builds chat messages applying the system prompt and instructions.
   - Appends a JSON structural template prefix to force JSON block returns.
   - Prepares input tensors and performs generation under `torch.no_grad()`.
   - Decodes tokens, reconstructs the JSON payload, and returns it to the client.

INPUTS (via POST /describe):
- `DescriptionRequest` Pydantic model:
  - `images_base64`: List of image string buffers.
  - `prompt_text`: The system instructions.
  - `temperature`: float value (defaults to 0.7).

OUTPUTS:
- `DescriptionResponse` Pydantic model:
  - `raw_responses`: List of VLM descriptions conforming to the requested JSON layout.

CRITICAL DEPENDENCIES:
- PyTorch with CUDA capability.
- BitsAndBytes (nf4 double quantization).
- HuggingFace Transformers (>= 4.45+ suggested for Gemma 4 support).
- FastAPI & Uvicorn.

AGENT GUIDELINE FOR MODIFICATION:
- The monkeypatch `patch_gemma4_unified` replaces `Gemma4UnifiedVisionEmbedder.forward` dynamically. 
  Never remove or disable it, as standard transformers code will fail to cast LayerNorm weights 
  properly under 4-bit quantization, leading to runtime failures.
- VRAM management is critical: if you observe out-of-memory (OOM) exceptions, decrease the 
  default batch size in the calling script or restrict Hugging Face input text paddings.
"""

import os
import sys
import base64
import io
import logging
from typing import List, Dict, Optional, Union

import torch
from transformers import AutoProcessor, AutoModelForMultimodalLM, BitsAndBytesConfig
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image, ImageFile

Image.MAX_IMAGE_PIXELS = None  # Disable limit to allow massive upscaled images
ImageFile.LOAD_TRUNCATED_IMAGES = True  # Allow reading truncated files

# Setup HF Cache Directory inside container
HF_CACHE_DIR: str = os.environ.get("HF_HOME", "/workspace/models/huggingface")
os.environ["HF_HOME"] = HF_CACHE_DIR
os.environ["HF_HUB_OFFLINE"] = os.environ.get("HF_HUB_OFFLINE", "1")
os.environ["TRANSFORMERS_OFFLINE"] = os.environ.get("TRANSFORMERS_OFFLINE", "1")
os.environ["TQDM_DISABLE"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

# Initialize logger
LOG_FILE: str = os.environ.get("VLM_LOG_FILE", "/workspace/local/uvicorn.log")
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

if root_logger.hasHandlers():
    root_logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

try:
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
except Exception as e:
    sys.stderr.write(f"Failed to add file log handler: {e}\n")

logger = logging.getLogger(__name__)

# Model default configs
MODEL_ID: str = os.environ.get("VLM_MODEL_ID", "google/gemma-4-12B-it")
QUANTIZED_MODEL_PATH: str = os.environ.get("VLM_QUANTIZED_MODEL_PATH", "/workspace/models/gemma-4-12b-it-quantized-4bit")

# Initialize Hugging Face Token from environment if available
hf_token: Optional[str] = os.environ.get("HF_TOKEN")
if hf_token:
    os.environ["HF_TOKEN"] = hf_token

app = FastAPI(title="Gemma 4 VLM Server", description="WSL2 Backend serving Gemma 4 VLM on-demand.")

# Global references for model and processor
model: Optional[AutoModelForMultimodalLM] = None
processor: Optional[AutoProcessor] = None
dtype: torch.dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32


class DescriptionRequest(BaseModel):
    """Schema representing an image processing payload request."""
    images_base64: List[str]
    prompt_text: str
    temperature: Optional[float] = 0.7


class DescriptionResponse(BaseModel):
    """Schema representing the server's generated response."""
    raw_responses: List[str]


def patch_gemma4_unified() -> None:
    """Monkey-patches Gemma4UnifiedVisionEmbedder.forward to resolve 4-bit LayerNorm casting bug.

    Returns:
        None
    """
    try:
        import transformers.models.gemma4_unified.modeling_gemma4_unified as m
        
        def patched_forward(self: "m.Gemma4UnifiedVisionEmbedder", pixel_values: torch.Tensor, image_position_ids: torch.Tensor) -> torch.Tensor:
            target_dtype = self.patch_ln1.weight.dtype
            hidden_states = self.patch_ln1(pixel_values.to(target_dtype))
            hidden_states = self.patch_dense(hidden_states)
            hidden_states = self.patch_ln2(hidden_states)

            clamped = image_position_ids.clamp(min=0).long()
            valid = (image_position_ids != -1).to(self.pos_embedding.dtype).unsqueeze(-1)
            axes = torch.arange(2, device=image_position_ids.device)
            pos_embs = (self.pos_embedding[clamped, axes] * valid).sum(-2)
            hidden_states = hidden_states + pos_embs
            hidden_states = self.pos_norm(hidden_states)

            hidden_states = self.multimodal_embedder(hidden_states)
            return hidden_states

        m.Gemma4UnifiedVisionEmbedder.forward = patched_forward
        logger.info("Successfully patched Gemma4UnifiedVisionEmbedder.")
    except Exception as e:
        logger.error(f"Failed to apply gemma4_unified monkeypatch: {e}")


@app.on_event("startup")
def load_model() -> None:
    """Loads the gemma-4-12B-it model and processor into VRAM on startup.

    If a pre-quantized 4-bit model checkpoint exists on disk, it loads it
    directly to skip the on-the-fly quantization CPU bottleneck.

    Returns:
        None
    """
    global model, processor
    patch_gemma4_unified()
    
    try:
        model_to_load = MODEL_ID
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            llm_int8_skip_modules=["lm_head", "embed_vision", "model.embed_vision", "embed_audio", "model.embed_audio"]
        )
        
        checkpoint_ready = os.path.exists(os.path.join(QUANTIZED_MODEL_PATH, "config.json"))
        if checkpoint_ready:
            logger.info(f"Found pre-quantized 4-bit checkpoint. Loading from: {QUANTIZED_MODEL_PATH}")
            model_to_load = QUANTIZED_MODEL_PATH
        else:
            logger.info(f"Pre-quantized checkpoint not found. Loading from base cache: {MODEL_ID}")

        model = AutoModelForMultimodalLM.from_pretrained(
            model_to_load, 
            torch_dtype=dtype, 
            quantization_config=bnb_config,
            device_map="cuda:0"
        )
        processor = AutoProcessor.from_pretrained(model_to_load)
        model.eval()
        logger.info("Gemma 4 model loaded successfully.")
        
        if not checkpoint_ready:
            logger.info(f"Auto-saving 4-bit quantized checkpoint to: {QUANTIZED_MODEL_PATH}")
            os.makedirs(QUANTIZED_MODEL_PATH, exist_ok=True)
            model.save_pretrained(QUANTIZED_MODEL_PATH)
            processor.save_pretrained(QUANTIZED_MODEL_PATH)
            logger.info("Auto-saving completed successfully!")
            
    except Exception as e:
        logger.critical(f"Failed to load VLM model: {e}")
        raise RuntimeError(f"Model initialization failed: {e}")


@app.post("/describe", response_model=DescriptionResponse)
def describe_images(request: DescriptionRequest) -> DescriptionResponse:
    """Processes base64 images and generates structural descriptions.

    Args:
        request: A Pydantic model containing the prompt and base64 encoded images.

    Returns:
        DescriptionResponse with the VLM's generated JSON strings.
    """
    if model is None or processor is None:
        raise HTTPException(status_code=503, detail="Model server is still initializing.")
    
    try:
        pil_images: List[Image.Image] = []
        for img_b64 in request.images_base64:
            img_bytes = base64.b64decode(img_b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            pil_images.append(img)
        
        if not pil_images:
            return DescriptionResponse(raw_responses=[])
        
        prompts: List[str] = []
        for _ in range(len(pil_images)):
            messages: List[Dict[str, Union[str, List[Dict[str, str]]]]] = [
                {
                    "role": "system",
                    "content": request.prompt_text
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {"type": "text", "text": (
                            "Analyze this image according to the archival schema. "
                            "The JSON object must have exactly the following structure:\n"
                            "{\n"
                            "  \"primary_subject\": \"...\",\n"
                            "  \"environment\": \"...\",\n"
                            "  \"suggested_tags\": []\n"
                            "}\n"
                            "Return only the raw JSON string. Do not include markdown formatting code blocks."
                        )}
                    ]
                }
            ]
            base_prompt: str = processor.apply_chat_template(messages, add_generation_prompt=True)
            prefill: str = '{\n  "primary_subject": "'
            prompts.append(base_prompt + prefill)
        
        inputs = processor(text=prompts, images=[[img] for img in pil_images], padding="longest", return_tensors="pt").to("cuda:0")
        
        if "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].to(dtype)
            
        gen_temp = request.temperature if request.temperature is not None else 0.7
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=True,
                temperature=gen_temp
            )
            
        input_len = inputs["input_ids"].shape[1]
        
        raw_responses: List[str] = []
        prefill = '{\n  "primary_subject": "'
        for out_tokens in output_ids:
            generated_ids = out_tokens[input_len:]
            decoded_text: str = processor.decode(generated_ids, skip_special_tokens=True).strip()
            full_response = prefill + decoded_text
            raw_responses.append(full_response)
            
        return DescriptionResponse(raw_responses=raw_responses)
        
    except Exception as e:
        logger.error(f"Inference processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Inference execution failed: {e}")
